import { describe, expect, it, vi } from 'vitest'

import { createTerminalOutputRelay } from './terminal-output-relay'

describe('createTerminalOutputRelay', () => {
  it('flushes startup output after the renderer subscribes, then streams live output', () => {
    const onData = vi.fn()
    const relay = createTerminalOutputRelay({ onData, onExit: vi.fn() })

    relay.emit('banner\r\n')
    relay.emit('$ ')
    expect(onData).not.toHaveBeenCalled()

    relay.subscribe()
    expect(onData.mock.calls.map(([data]) => data)).toEqual(['banner\r\n', '$ '])

    relay.emit('pwd\r\n')
    expect(onData.mock.calls.map(([data]) => data)).toEqual(['banner\r\n', '$ ', 'pwd\r\n'])
  })

  it('delivers an early exit after buffered output once the renderer subscribes', () => {
    const events: string[] = []

    const relay = createTerminalOutputRelay({
      onData: data => events.push(`data:${data}`),
      onExit: ({ code }) => events.push(`exit:${code}`)
    })

    relay.emit('ssh: connection refused\r\n')
    relay.exit({ code: 255, signal: null })

    expect(events).toEqual([])

    relay.subscribe()
    expect(events).toEqual(['data:ssh: connection refused\r\n', 'exit:255'])
  })

  it('does not replay startup output on duplicate subscribe calls', () => {
    const onData = vi.fn()
    const relay = createTerminalOutputRelay({ onData, onExit: vi.fn() })

    relay.emit('$ ')
    relay.subscribe()
    relay.subscribe()

    expect(onData).toHaveBeenCalledOnce()
  })
})
