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

/** Buffer PTY output until the renderer attaches its id-scoped listeners. */
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
      pending.splice(0).forEach(send)
    }
  }
}
