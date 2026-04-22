import { EventEmitter } from 'node:events'

import type { GatewayEvent } from './gatewayTypes.js'
import { LocalSubprocessTransport } from './transport/LocalSubprocessTransport.js'
import type { Transport } from './transport/Transport.js'

/**
 * GatewayClient is a thin coordinator over a `Transport`. It exists to:
 *
 *  1. Preserve the (large) surface area the rest of the TUI imports from this
 *     module — `request`, `on`, `off`, `kill`, `start`, `drain`, `getLogTail`.
 *  2. Let callers stay decoupled from the concrete transport (local subprocess
 *     vs. remote relay) chosen at `entry.tsx` time.
 *
 * The pre-Phase-2 body of this class moved verbatim into
 * `transport/LocalSubprocessTransport.ts`. See docs/relay-protocol.md §3.7
 * and docs/plans/2026-04-22-desktop-tui-mvp.md for the broader picture.
 */
export class GatewayClient extends EventEmitter {
  private transport: Transport

  constructor(transport?: Transport) {
    super()
    this.setMaxListeners(0)

    this.transport = transport ?? new LocalSubprocessTransport()

    // Re-emit events + exit from the underlying transport so the `on`/`off`
    // surface on GatewayClient keeps working unchanged.
    this.transport.on('event', (ev: GatewayEvent) => this.emit('event', ev))
    this.transport.on('exit', (code: number | null) => this.emit('exit', code))
  }

  start() {
    this.transport.start()
  }

  request<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    return this.transport.request<T>(method, params)
  }

  drain() {
    this.transport.drain()
  }

  getLogTail(limit = 20): string {
    return this.transport.getLogTail(limit)
  }

  kill() {
    this.transport.kill()
  }
}
