import { describe, expect, it } from 'vitest'

import { LocalSubprocessTransport } from '../LocalSubprocessTransport.js'

/**
 * These are smoke tests: we don't spawn a real `tui_gateway` (that requires
 * Python + the host repo and belongs in an integration harness). We only
 * assert the public surface the rest of the TUI relies on compiles, exists,
 * and is safe in the "not yet started" state — matching the pre-refactor
 * behavior contract.
 *
 * Full subprocess round-trip coverage lives upstream in hermes-agent's
 * Python-side `tui_gateway` tests. The Node transport's job is just to
 * speak line-delimited JSON-RPC faithfully, and that logic is now the
 * same code path either transport runs.
 */
describe('LocalSubprocessTransport', () => {
  it('constructs without spawning', () => {
    const tr = new LocalSubprocessTransport()
    expect(tr.getLogTail()).toBe('')
  })

  it('kill() before start() is safe', () => {
    const tr = new LocalSubprocessTransport()
    expect(() => tr.kill()).not.toThrow()
  })

  it('drain() on a fresh instance emits nothing synchronously', () => {
    const tr = new LocalSubprocessTransport()
    const events: unknown[] = []
    tr.on('event', ev => events.push(ev))
    tr.drain()
    expect(events).toEqual([])
  })

  it('exposes Transport contract methods', () => {
    const tr = new LocalSubprocessTransport()
    expect(typeof tr.start).toBe('function')
    expect(typeof tr.request).toBe('function')
    expect(typeof tr.on).toBe('function')
    expect(typeof tr.off).toBe('function')
    expect(typeof tr.drain).toBe('function')
    expect(typeof tr.getLogTail).toBe('function')
    expect(typeof tr.kill).toBe('function')
  })
})
