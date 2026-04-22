import type { GatewayEvent } from '../gatewayTypes.js'

/**
 * Abstract transport for the TUI ↔ tui_gateway JSON-RPC stream.
 *
 * Two concrete impls ship today:
 *  - LocalSubprocessTransport — spawns `python -m tui_gateway.entry` locally (default).
 *  - RelayTransport — pipes JSON-RPC to a remote tui_gateway subprocess via the
 *    hermes-relay `tui` channel over WSS.
 *
 * The Python server is endpoint-agnostic; both transports speak the same
 * line-delimited JSON-RPC 2.0 wire format. Only the carrier changes.
 */
export interface Transport {
  /** Drop in-flight state and start the carrier (spawn subprocess / open socket). */
  start(): void
  /** Send a JSON-RPC request; resolves with `result` or rejects with the server's error. */
  request<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>
  /** Attach event/exit listeners. */
  on(event: 'event', handler: (ev: GatewayEvent) => void): void
  on(event: 'exit', handler: (code: number | null) => void): void
  /** Detach. */
  off(event: 'event', handler: (ev: GatewayEvent) => void): void
  off(event: 'exit', handler: (code: number | null) => void): void
  /** Flush buffered events to listeners — call once after attaching. */
  drain(): void
  /** Tail of captured stderr / transport log for `/logs` slash command. */
  getLogTail(limit?: number): string
  /** Stop the carrier. Safe to call multiple times. */
  kill(): void
}
