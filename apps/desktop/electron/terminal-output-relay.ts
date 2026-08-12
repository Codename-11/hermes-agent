interface TerminalExit {
  code: number
  signal: number | null
}

type TerminalEvent = { data: string; kind: 'data' } | { exit: TerminalExit; kind: 'exit' }

export interface TerminalOutputRelay {
  emit: (data: string) => void
  exit: (payload: TerminalExit) => void
  subscribe: () => void
}

/**
 * Hold PTY output until the renderer has attached its session-scoped IPC
 * listener. The renderer cannot know that channel until terminal:start returns
 * the generated id, so forwarding immediately can drop the shell's first prompt.
 */
export function createTerminalOutputRelay({
  onData,
  onExit
}: {
  onData: (data: string) => void
  onExit: (payload: TerminalExit) => void
}): TerminalOutputRelay {
  let subscribed = false
  const pending: TerminalEvent[] = []

  const send = (event: TerminalEvent) => {
    if (event.kind === 'data') {
      onData(event.data)
    } else {
      onExit(event.exit)
    }
  }

  return {
    emit(data) {
      if (subscribed) {
        onData(data)
      } else {
        pending.push({ data, kind: 'data' })
      }
    },
    exit(payload) {
      if (subscribed) {
        onExit(payload)
      } else {
        pending.push({ exit: payload, kind: 'exit' })
      }
    },
    subscribe() {
      if (subscribed) {
        return
      }

      subscribed = true

      for (const event of pending.splice(0)) {
        send(event)
      }
    }
  }
}
