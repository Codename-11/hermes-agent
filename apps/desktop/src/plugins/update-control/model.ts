export interface UpdateSummary {
  supported?: boolean
  updateAvailable?: boolean
  behind?: number
}

export function hasUpdate(status: UpdateSummary | null | undefined): boolean {
  return status?.supported === true && (status.updateAvailable === true || (status.behind ?? 0) > 0)
}

export function shortSha(value?: null | string): string {
  return value ? value.slice(0, 8) : '—'
}

export function friendlyError(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }

  if (typeof error === 'string' && error.trim()) {
    return error
  }

  return 'Update information is unavailable right now.'
}
