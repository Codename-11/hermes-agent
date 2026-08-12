export interface UpdateOperationRun<T> {
  acquired: boolean
  active?: string
  value?: T
}

export interface UpdateOperationCoordinator {
  active: () => string | null
  run: <T>(name: string, operation: () => Promise<T>) => Promise<UpdateOperationRun<T>>
}

/** Serializes update lifecycle mutations owned by Electron main. */
export function createUpdateOperationCoordinator(): UpdateOperationCoordinator {
  let current: string | null = null

  return {
    active: () => current,
    run: async <T>(name: string, operation: () => Promise<T>) => {
      if (current) {
        return { acquired: false, active: current }
      }

      current = name

      try {
        return { acquired: true, value: await operation() }
      } finally {
        if (current === name) {
          current = null
        }
      }
    }
  }
}
