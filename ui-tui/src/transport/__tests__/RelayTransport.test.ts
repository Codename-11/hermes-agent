import { describe, expect, it } from 'vitest'

import { RelayTransport } from '../RelayTransport.js'

// ── Fake WebSocket ────────────────────────────────────────────────────
//
// Implements just enough of the WHATWG WebSocket interface to exercise
// RelayTransport's envelope wrap/unwrap logic. We capture all `send()`
// calls in `sent` and expose `deliver(frame)` so the test can drive
// inbound frames.

type Listener = (ev?: unknown) => void

class FakeWS {
  readyState = 0 // CONNECTING
  sent: string[] = []
  private listeners = new Map<string, Listener[]>()

  constructor() {}

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = 3 // CLOSED
    this.fire('close', { code: 1000, reason: 'test-close' })
  }

  addEventListener(type: string, listener: Listener) {
    const bucket = this.listeners.get(type) ?? []
    bucket.push(listener)
    this.listeners.set(type, bucket)
  }

  fire(type: string, ev?: unknown) {
    for (const l of this.listeners.get(type) ?? []) {l(ev)}
  }

  deliver(envelope: Record<string, unknown>) {
    this.fire('message', { data: JSON.stringify(envelope) })
  }

  openAndAck(serverVersion = '0.8.0-alpha', sessionToken = 'mock-token-uuid') {
    this.readyState = 1
    this.fire('open')
    this.deliver({
      channel: 'system',
      type: 'auth.ok',
      id: 'auth-1',
      payload: { session_token: sessionToken, server_version: serverVersion, expires_at: null, grants: {} }
    })
  }
}

const buildTransport = (overrides: Partial<{ pairingCode: string; sessionToken: string }> = {}) => {
  const fake = new FakeWS()

  const tr = new RelayTransport({
    url: 'ws://test.invalid:8767/ws',
    pairingCode: overrides.pairingCode,
    sessionToken: overrides.sessionToken ?? (overrides.pairingCode ? undefined : 'preexisting-token'),
    deviceName: 'test-device',
    deviceId: 'test-id',
    wsFactory: () => fake as unknown as WebSocket
  })

  return { tr, fake }
}

describe('RelayTransport', () => {
  it('sends auth envelope with sessionToken on open', () => {
    const { tr, fake } = buildTransport({ sessionToken: 'tok-abc' })
    tr.start()
    fake.readyState = 1
    fake.fire('open')

    expect(fake.sent).toHaveLength(1)
    const authEnv = JSON.parse(fake.sent[0]!)
    expect(authEnv.channel).toBe('system')
    expect(authEnv.type).toBe('auth')
    expect(authEnv.payload.session_token).toBe('tok-abc')
    expect(authEnv.payload.device_name).toBe('test-device')
  })

  it('prefers pairingCode when both are present', () => {
    const { tr, fake } = buildTransport({ pairingCode: 'ABC123', sessionToken: 'tok-abc' })
    tr.start()
    fake.readyState = 1
    fake.fire('open')

    const authEnv = JSON.parse(fake.sent[0]!)
    expect(authEnv.payload.pairing_code).toBe('ABC123')
    expect(authEnv.payload.session_token).toBeUndefined()
  })

  it('stores session_token from auth.ok and sends tui.attach', () => {
    const { tr, fake } = buildTransport()
    tr.start()
    fake.openAndAck('1.2.3', 'fresh-token')

    expect(tr.sessionToken).toBe('fresh-token')
    expect(tr.serverVersion).toBe('1.2.3')

    // Most recent frame should be the tui.attach envelope.
    const attach = JSON.parse(fake.sent[fake.sent.length - 1]!)
    expect(attach.channel).toBe('tui')
    expect(attach.type).toBe('tui.attach')
    expect(typeof attach.payload.cols).toBe('number')
    expect(typeof attach.payload.rows).toBe('number')
  })

  it('wraps request() in a tui.rpc.request envelope and resolves on matching response', async () => {
    const { tr, fake } = buildTransport()
    tr.start()
    fake.openAndAck()

    const pending = tr.request<{ ok: boolean }>('ping', { foo: 1 })

    // Inspect the last sent frame — should be a tui.rpc.request.
    const rpcFrame = JSON.parse(fake.sent[fake.sent.length - 1]!)
    expect(rpcFrame.channel).toBe('tui')
    expect(rpcFrame.type).toBe('tui.rpc.request')
    expect(rpcFrame.payload.method).toBe('ping')
    expect(rpcFrame.payload.params).toEqual({ foo: 1 })
    expect(rpcFrame.payload.jsonrpc).toBe('2.0')
    expect(typeof rpcFrame.payload.id).toBe('string')

    // Deliver matching response via tui.rpc.response.
    fake.deliver({
      channel: 'tui',
      type: 'tui.rpc.response',
      id: 'server-gen',
      payload: { id: rpcFrame.payload.id, jsonrpc: '2.0', result: { ok: true } }
    })

    await expect(pending).resolves.toEqual({ ok: true })
  })

  it('rejects request() when response carries error', async () => {
    const { tr, fake } = buildTransport()
    tr.start()
    fake.openAndAck()

    const pending = tr.request('boom', {})
    const rpcFrame = JSON.parse(fake.sent[fake.sent.length - 1]!)

    fake.deliver({
      channel: 'tui',
      type: 'tui.rpc.response',
      id: 'err-1',
      payload: { id: rpcFrame.payload.id, jsonrpc: '2.0', error: { message: 'nope' } }
    })

    await expect(pending).rejects.toThrow('nope')
  })

  it('unwraps tui.rpc.event envelopes and publishes GatewayEvents', () => {
    const { tr, fake } = buildTransport()
    const events: unknown[] = []
    tr.on('event', ev => events.push(ev))
    tr.start()
    fake.openAndAck()
    tr.drain()

    fake.deliver({
      channel: 'tui',
      type: 'tui.rpc.event',
      id: 'evt-1',
      payload: {
        jsonrpc: '2.0',
        method: 'event',
        params: { type: 'gateway.stderr', payload: { line: 'hi from remote' } }
      }
    })

    expect(events).toContainEqual({ type: 'gateway.stderr', payload: { line: 'hi from remote' } })
  })

  it('buffers events emitted before drain()', () => {
    const { tr, fake } = buildTransport()
    tr.start()
    fake.openAndAck()

    fake.deliver({
      channel: 'tui',
      type: 'tui.rpc.event',
      id: 'evt-1',
      payload: { jsonrpc: '2.0', method: 'event', params: { type: 'gateway.stderr', payload: { line: 'buffered' } } }
    })

    const received: unknown[] = []
    tr.on('event', ev => received.push(ev))
    // Not drained yet.
    expect(received).toEqual([])

    tr.drain()
    expect(received).toContainEqual({ type: 'gateway.stderr', payload: { line: 'buffered' } })
  })

  it('surfaces tui.error as a teardown + stderr event', () => {
    const { tr, fake } = buildTransport()
    const events: unknown[] = []
    const exits: (number | null)[] = []
    tr.on('event', ev => events.push(ev))
    tr.on('exit', c => exits.push(c))
    tr.start()
    fake.openAndAck()
    tr.drain()

    fake.deliver({
      channel: 'tui',
      type: 'tui.error',
      id: 'err-1',
      payload: { message: 'subprocess died' }
    })

    const hasStderr = events.some(
      e =>
        typeof e === 'object' &&
        e !== null &&
        (e as { type?: string }).type === 'gateway.stderr' &&
        ((e as { payload?: { line?: string } }).payload?.line ?? '').includes('subprocess died')
    )

    expect(hasStderr).toBe(true)
    expect(exits.length).toBeGreaterThan(0)
  })

  it('auth.fail tears down with the server reason', () => {
    const { tr, fake } = buildTransport()
    const exits: (number | null)[] = []
    tr.on('exit', c => exits.push(c))
    tr.start()
    fake.readyState = 1
    fake.fire('open')

    fake.deliver({
      channel: 'system',
      type: 'auth.fail',
      id: 'x',
      payload: { reason: 'bad code' }
    })
    tr.drain()

    expect(exits.length).toBeGreaterThan(0)
    expect(tr.getLogTail()).toContain('bad code')
  })

  it('request() before auth rejects', async () => {
    const { tr } = buildTransport()
    tr.start()
    await expect(tr.request('ping')).rejects.toThrow(/not authenticated/)
  })

  it('responds to system.ping with system.pong', () => {
    const { tr, fake } = buildTransport()
    tr.start()
    fake.openAndAck()
    const before = fake.sent.length

    fake.deliver({ channel: 'system', type: 'ping', id: 'p-1', payload: { ts: 42 } })
    const pong = JSON.parse(fake.sent[before]!)
    expect(pong.channel).toBe('system')
    expect(pong.type).toBe('pong')
    expect(pong.payload.ts).toBe(42)
  })

  it('fires onAuthSuccess observers with the minted token + version', () => {
    const { tr, fake } = buildTransport()
    const calls: Array<[string, null | string]> = []
    tr.onAuthSuccess((token, version) => calls.push([token, version]))
    tr.start()
    fake.openAndAck('0.8.0-alpha', 'minted-tok')
    expect(calls).toEqual([['minted-tok', '0.8.0-alpha']])
  })

  it('swallows observer exceptions so auth still completes', () => {
    const { tr, fake } = buildTransport()
    tr.onAuthSuccess(() => {
      throw new Error('boom')
    })
    tr.start()
    fake.openAndAck()
    // getAuthInfo reflects the successful auth even though the cb threw.
    expect(tr.getAuthInfo()).toEqual({ serverVersion: '0.8.0-alpha', token: 'mock-token-uuid' })
  })

  it('sendResize emits a tui.resize envelope after auth', () => {
    const { tr, fake } = buildTransport()
    tr.start()
    fake.openAndAck()
    const before = fake.sent.length
    tr.sendResize(120, 40)
    const resize = JSON.parse(fake.sent[before]!)
    expect(resize.channel).toBe('tui')
    expect(resize.type).toBe('tui.resize')
    expect(resize.payload).toEqual({ cols: 120, rows: 40 })
  })

  it('sendResize no-ops when called before auth', () => {
    const { tr, fake } = buildTransport()
    tr.start()
    const before = fake.sent.length
    tr.sendResize(100, 30)
    expect(fake.sent.length).toBe(before)
  })
})
