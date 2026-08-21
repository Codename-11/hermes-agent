export interface OauthLoginCoordinator {
  isPending: (baseUrl: string) => boolean
  run: <T>(baseUrl: string, operation: () => Promise<T>) => Promise<T>
}

/**
 * Keep one interactive OAuth login in flight per normalized gateway.
 *
 * Electron main owns this process-wide boundary so concurrent renderer callers
 * for the same gateway share one PKCE exchange. Success and failure both clear
 * the entry, allowing an explicit retry to start a fresh login.
 */
export function createOauthLoginCoordinator(): OauthLoginCoordinator {
  const pending = new Map<string, Promise<unknown>>()

  return {
    isPending: baseUrl => pending.has(baseUrl),
    run: <T>(baseUrl: string, operation: () => Promise<T>) => {
      const existing = pending.get(baseUrl)

      if (existing) {
        return existing as Promise<T>
      }

      let promise: Promise<T>

      try {
        promise = operation()
      } catch (error) {
        promise = Promise.reject(error)
      }

      pending.set(baseUrl, promise)

      const clear = () => {
        if (pending.get(baseUrl) === promise) {
          pending.delete(baseUrl)
        }
      }

      void promise.then(clear, clear)

      return promise
    }
  }
}
