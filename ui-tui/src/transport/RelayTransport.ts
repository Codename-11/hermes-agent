import { randomUUID } from 'node:crypto'
import { EventEmitter } from 'node:events'

import type { GatewayEvent } from '../gatewayTypes.js'
import { CircularBuffer } from '../lib/circularBuffer.js'

import type { Transport } from './Transport.js'

const MAX_LOG_LINES = 200
const MAX_LOG_LINE_BYTES = 4096
const MAX_BUFFERED_EVENTS = 2000
const REQUEST_TIMEOUT_MS = Math.max(30000, parseInt(process.env.HERMES_TUI_RPC_TIMEOUT_MS ?? '120000', 10) || 120000)
const AUTH_TIMEOUT_MS = Math.max(5000, parseInt(process.env.HERMES_RELAY_AUTH_TIMEOUT_MS ?? '15000', 10) || 15000)

const truncateLine = (line: string) =>
  line.length > MAX_LOG_LINE_BYTES ? `${line.slice(0, MAX_LOG_LINE_BYTES)}… [truncated ${line.length} bytes]` : line

const asGatewayEvent = (value: unknown): GatewayEvent | null =>
  value && typeof value === 'object' && !Array.isArray(value) && typeof (value as { type?: unknown }).type === 'string'
    ? (value as GatewayEvent)
    : null

interface Pending {
  id: string
  method: string
  reject: (e: Error) => void
  resolve: (v: unknown) => void
  timeout: ReturnType<typeof setTimeout>
}

/**
 * Minimal subset of the WHATWG WebSocket interface we rely on. Node 20+
 * ships a global `WebSocket`; we type it loosely here to avoid a hard
 * dep on lib.dom.d.ts (this project is Node-only).
 */
interface WSMessageEvent { data: unknown }
interface WSCloseEvent { code: number; reason: string }
interface WSErrorEvent { message?: string }

interface WSLike {
  readyState: number
  send(data: string): void
  close(code?: number, reason?: string): void
  addEventListener(type: 'open', listener: () => void): void
  addEventListener(type: 'message', listener: (ev: WSMessageEvent) => void): void
  addEventListener(type: 'close', listener: (ev: WSCloseEvent) => void): void
  addEventListener(type: 'error', listener: (ev: WSErrorEvent) => void): void
}

type WSFactory = (url: string) => WSLike

/**
 * Default factory: use Node 20+'s built-in global `WebSocket` (undici).
 * Tests inject a mock factory instead.
 */
const defaultWSFactory: WSFactory = url => {
   
  const Ctor = (globalThis as any).WebSocket

  if (typeof Ctor !== 'function') {
    throw new Error('RelayTransport: global WebSocket not available (Node < 21 without --experimental-websocket?)')
  }

  return new Ctor(url) as WSLike
}

export interface RelayTransportConfig {
  url: string
  /** One-time pairing code. Mutually exclusive with sessionToken. */
  pairingCode?: string
  /** Previously-minted session token for reconnection. */
  sessionToken?: string
  /** Human-readable label for the "Paired Devices" list. */
  deviceName?: string
  /** Stable per-install identifier. */
  deviceId?: string
  /** Requested session lifetime in seconds (0 = never expire). */
  ttlSeconds?: number
  /** Test hook — injects a fake WebSocket. */
  wsFactory?: WSFactory
}

/**
 * RelayTransport pipes JSON-RPC to a remote `tui_gateway` subprocess via the
 * hermes-relay `tui` channel (see docs/relay-protocol.md §3.7).
 *
 * Outbound JSON-RPC requests are wrapped in `tui.rpc.request` envelopes.
 * Inbound `tui.rpc.response` and `tui.rpc.event` envelopes are unwrapped
 * back to the flat JSON-RPC shape the rest of the TUI expects.
 *
 * Reconnect logic is intentionally minimal for the Phase 2 MVP: on disconnect,
 * pending requests reject and `exit` fires. Phase 3 will add backoff +
 * `resume_session_id` re-attach.
 */
export class RelayTransport extends EventEmitter implements Transport {
  private ws: WSLike | null = null
  private wsFactory: WSFactory
  private cfg: RelayTransportConfig
  private reqId = 0
  private logs = new CircularBuffer<string>(MAX_LOG_LINES)
  private pending = new Map<string, Pending>()
  private bufferedEvents = new CircularBuffer<GatewayEvent>(MAX_BUFFERED_EVENTS)
  private pendingExit: number | null | undefined
  private subscribed = false
  private authResolved = false
  private authTimer: ReturnType<typeof setTimeout> | null = null
  /** Session token as returned by the relay after `auth.ok`. Callers may read this after a successful connection to persist for reconnect. */
  sessionToken: string | null = null
  /** Server-reported version for diagnostics. */
  serverVersion: string | null = null
  /** Observers notified exactly once per `auth.ok`. */
  private authSuccessObservers: Array<(token: string, serverVersion: string | null) => void> = []

  constructor(cfg: RelayTransportConfig) {
    super()
    this.setMaxListeners(0)
    this.cfg = cfg
    this.wsFactory = cfg.wsFactory ?? defaultWSFactory
    this.sessionToken = cfg.sessionToken ?? null
  }

  /**
   * Register a callback fired once per successful auth handshake. Used by
   * the CLI entry point to persist the relay-minted session token into
   * `~/.hermes/remote-sessions.json` for reconnect. Thrown exceptions are
   * swallowed so persistence failures never take down the transport.
   */
  onAuthSuccess(cb: (token: string, serverVersion: string | null) => void): void {
    this.authSuccessObservers.push(cb)
  }

  /**
   * Send a `tui.resize` envelope. Safe to call before auth — no-ops if the
   * socket isn't attached or auth hasn't completed (the subprocess will
   * pick up the correct size from the initial `tui.attach` payload instead).
   */
  sendResize(cols: number, rows: number): void {
    if (!this.ws || !this.authResolved) {return}
    this.sendEnvelope('tui', 'tui.resize', { cols, rows })
  }

  /** Minimal getter for callers that prefer polling over callbacks. */
  getAuthInfo(): { serverVersion: string | null; token: string } | null {
    if (!this.authResolved || !this.sessionToken) {return null}

    return { serverVersion: this.serverVersion, token: this.sessionToken }
  }

  start() {
    this.pendingExit = undefined
    this.authResolved = false
    this.tornDown = false

    if (this.authTimer) {
      clearTimeout(this.authTimer)
    }

    this.authTimer = setTimeout(() => {
      if (this.authResolved) {return}
      const msg = `auth timed out after ${AUTH_TIMEOUT_MS}ms`
      this.pushLog(`[auth] ${msg} (url=${this.cfg.url})`)
      this.publish({ type: 'gateway.start_timeout', payload: {} })
      this.teardown(-1, msg)
    }, AUTH_TIMEOUT_MS)

    let ws: WSLike

    try {
      ws = this.wsFactory(this.cfg.url)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      this.pushLog(`[ws] factory failed: ${msg}`)
      this.publish({ type: 'gateway.stderr', payload: { line: `[ws] ${msg}` } })
      this.teardown(-1, msg)

      return
    }

    this.ws = ws

    ws.addEventListener('open', () => {
      this.pushLog(`[ws] open → ${this.cfg.url}`)
      this.sendAuth()
    })

    ws.addEventListener('message', ev => {
      const raw = typeof ev.data === 'string' ? ev.data : String(ev.data ?? '')
      this.handleFrame(raw)
    })

    ws.addEventListener('close', ev => {
      this.pushLog(`[ws] close code=${ev.code} reason=${ev.reason || ''}`)
      this.teardown(ev.code, ev.reason)
    })

    ws.addEventListener('error', ev => {
      const msg = ev?.message ?? 'WebSocket error'
      this.pushLog(`[ws] error: ${msg}`)
      this.publish({ type: 'gateway.stderr', payload: { line: `[ws] ${msg}` } })
      // `close` will typically follow and trigger teardown.
    })
  }

  private sendAuth() {
    if (!this.ws) {return}

    const payload: Record<string, unknown> = {}

    if (this.cfg.pairingCode) {
      payload.pairing_code = this.cfg.pairingCode
    } else if (this.cfg.sessionToken) {
      payload.session_token = this.cfg.sessionToken
    } else {
      const msg = 'RelayTransport: neither pairingCode nor sessionToken provided'
      this.pushLog(`[auth] ${msg}`)
      this.publish({ type: 'gateway.stderr', payload: { line: msg } })
      this.teardown(-1, msg)

      return
    }

    if (this.cfg.deviceName) {payload.device_name = this.cfg.deviceName}

    if (this.cfg.deviceId) {payload.device_id = this.cfg.deviceId}

    if (typeof this.cfg.ttlSeconds === 'number') {payload.ttl_seconds = this.cfg.ttlSeconds}

    this.sendEnvelope('system', 'auth', payload)
  }

  private sendEnvelope(channel: string, type: string, payload: Record<string, unknown>, id?: string) {
    if (!this.ws) {return}
    const envelope = { channel, type, id: id ?? randomUUID(), payload }

    try {
      this.ws.send(JSON.stringify(envelope))
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      this.pushLog(`[ws] send failed: ${msg}`)
    }
  }

  private handleFrame(raw: string) {
    let msg: Record<string, unknown>

    try {
      msg = JSON.parse(raw) as Record<string, unknown>
    } catch {
      this.pushLog(`[protocol] malformed frame: ${raw.slice(0, 240)}`)
      this.publish({ type: 'gateway.protocol_error', payload: { preview: raw.slice(0, 240) } })

      return
    }

    const channel = typeof msg.channel === 'string' ? msg.channel : ''
    const type = typeof msg.type === 'string' ? msg.type : ''
    const payload = (msg.payload ?? {}) as Record<string, unknown>

    if (channel === 'system') {
      this.handleSystem(type, payload)

      return
    }

    if (channel === 'tui') {
      this.handleTui(type, payload)

      return
    }

    // Unknown channel — log, don't crash. Pairing pushes etc. can arrive here.
    this.pushLog(`[ws] ignoring ${channel}:${type}`)
  }

  private handleSystem(type: string, payload: Record<string, unknown>) {
    if (type === 'auth.ok') {
      this.authResolved = true

      if (this.authTimer) {
        clearTimeout(this.authTimer)
        this.authTimer = null
      }

      const token = payload.session_token

      if (typeof token === 'string') {this.sessionToken = token}
      const ver = payload.server_version

      if (typeof ver === 'string') {this.serverVersion = ver}
      this.pushLog(`[auth] ok (server ${this.serverVersion ?? '?'})`)

      // Fire auth-success observers so the CLI entry can persist the token.
      // Swallow observer errors — persistence hiccups must not take down
      // the transport.
      if (this.sessionToken) {
        for (const cb of this.authSuccessObservers) {
          try {
            cb(this.sessionToken, this.serverVersion)
          } catch {
            /* ignore — see comment above */
          }
        }
      }

      // Attach the TUI channel. Subprocess on the relay is spawned on receipt.
      this.sendEnvelope('tui', 'tui.attach', {
        cols: process.stdout.columns ?? 80,
        rows: process.stdout.rows ?? 24
      })

      return
    }

    if (type === 'auth.fail') {
      const reason = typeof payload.reason === 'string' ? payload.reason : 'auth failed'
      this.pushLog(`[auth] fail: ${reason}`)
      this.publish({ type: 'gateway.stderr', payload: { line: `[auth] ${reason}` } })
      this.teardown(-1, reason)

      return
    }

    if (type === 'ping') {
      this.sendEnvelope('system', 'pong', { ts: typeof payload.ts === 'number' ? payload.ts : Date.now() })

      return
    }
  }

  private handleTui(type: string, payload: Record<string, unknown>) {
    if (type === 'tui.attached') {
      this.pushLog(`[tui] attached pid=${payload.pid ?? '?'} server=${payload.server_version ?? '?'}`)

      // Tell the UI this is effectively "ready" so startup timers clear. The
      // subprocess will additionally emit its own `gateway.ready` event.
      return
    }

    if (type === 'tui.rpc.response') {
      // Unwrap back to flat JSON-RPC and dispatch through the normal path.
      this.dispatchRpc(payload)

      return
    }

    if (type === 'tui.rpc.event') {
      const params = payload.params

      const ev = asGatewayEvent(
        (params && typeof params === 'object' && !Array.isArray(params))
          ? (params as Record<string, unknown>)
          : null
      )

      if (ev) {this.publish(ev)}

      return
    }

    if (type === 'tui.error') {
      const message = typeof payload.message === 'string' ? payload.message : 'tui channel error'
      this.pushLog(`[tui] error: ${message}`)
      this.publish({ type: 'gateway.stderr', payload: { line: `[tui] ${message}` } })
      this.teardown(-1, message)

      return
    }
  }

  private dispatchRpc(msg: Record<string, unknown>) {
    const id = msg.id as string | undefined
    const p = id ? this.pending.get(id) : undefined

    if (!p) {return}
    this.settle(p, msg.error ? this.toError(msg.error) : null, msg.result)
  }

  private toError(raw: unknown): Error {
    const err = raw as { message?: unknown } | null | undefined

    return new Error(typeof err?.message === 'string' ? err.message : 'request failed')
  }

  private settle(p: Pending, err: Error | null, result: unknown) {
    clearTimeout(p.timeout)
    this.pending.delete(p.id)

    if (err) {p.reject(err)}
    else {p.resolve(result)}
  }

  private publish(ev: GatewayEvent) {
    if (this.subscribed) {
      this.emit('event', ev)

      return
    }

    this.bufferedEvents.push(ev)
  }

  private pushLog(line: string) {
    this.logs.push(truncateLine(line))
  }

  private rejectPending(err: Error) {
    for (const p of this.pending.values()) {
      clearTimeout(p.timeout)
      p.reject(err)
    }

    this.pending.clear()
  }

  private onTimeout = (id: string) => {
    const p = this.pending.get(id)

    if (p) {
      this.pending.delete(id)
      p.reject(new Error(`timeout: ${p.method}`))
    }
  }

  private tornDown = false

  private teardown(code: number | null, reason: string) {
    if (this.tornDown) {return}
    this.tornDown = true

    if (this.authTimer) {
      clearTimeout(this.authTimer)
      this.authTimer = null
    }

    this.rejectPending(new Error(`relay disconnected: ${reason || 'unknown'}`))
    const ws = this.ws
    this.ws = null

    try {
      ws?.close()
    } catch {
      /* ignore */
    }

    if (this.subscribed) {
      this.emit('exit', code)
    } else {
      this.pendingExit = code
    }
  }

  drain() {
    this.subscribed = true

    for (const ev of this.bufferedEvents.drain()) {
      this.emit('event', ev)
    }

    if (this.pendingExit !== undefined) {
      const code = this.pendingExit
      this.pendingExit = undefined
      this.emit('exit', code)
    }
  }

  getLogTail(limit = 20): string {
    return this.logs.tail(Math.max(1, limit)).join('\n')
  }

  request<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.ws) {
      return Promise.reject(new Error('relay transport not connected'))
    }

    // readyState 1 = OPEN. We also allow requests before OPEN by queuing via
    // promise — but for MVP we require OPEN + authResolved, matching parity
    // with local transport's "no send until proc.stdin exists".
    if (!this.authResolved) {
      return Promise.reject(new Error('relay not authenticated yet'))
    }

    const id = `r${++this.reqId}`

    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(this.onTimeout, REQUEST_TIMEOUT_MS, id)
      timeout.unref?.()

      this.pending.set(id, {
        id,
        method,
        reject,
        resolve: v => resolve(v as T),
        timeout
      })

      try {
        this.sendEnvelope('tui', 'tui.rpc.request', { id, jsonrpc: '2.0', method, params })
      } catch (e) {
        const pending = this.pending.get(id)

        if (pending) {
          clearTimeout(pending.timeout)
          this.pending.delete(id)
        }

        reject(e instanceof Error ? e : new Error(String(e)))
      }
    })
  }

  kill() {
    this.teardown(null, 'kill')
  }
}
