export interface NativeTokenRefreshCoordinator {
  run<T>(baseUrl: string, refresh: () => Promise<T>): Promise<T>
}

function refreshScope(baseUrl: string): string {
  const parsed = new URL(baseUrl)

  parsed.search = ''
  parsed.hash = ''
  parsed.pathname = parsed.pathname.replace(/\/+$/, '') || '/'

  return parsed.toString()
}

/** Share one rotating refresh-token exchange across callers for the same gateway. */
export function createNativeTokenRefreshCoordinator(): NativeTokenRefreshCoordinator {
  const inFlight = new Map<string, Promise<unknown>>()

  return {
    run<T>(baseUrl: string, refresh: () => Promise<T>): Promise<T> {
      const scope = refreshScope(baseUrl)
      const existing = inFlight.get(scope)

      if (existing) {
        return existing as Promise<T>
      }

      const pending = Promise.resolve().then(refresh)
      inFlight.set(scope, pending)
      void pending.finally(() => {
        if (inFlight.get(scope) === pending) {
          inFlight.delete(scope)
        }
      }).catch(() => undefined)

      return pending
    }
  }
}
