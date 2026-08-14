export type PaneCloseAction = 'closer' | 'dismiss' | 'disable-plugin'

export function resolvePaneCloseAction(options: {
  hasCloser: boolean
  closeBehavior?: string
  source?: string
  sameSourceCount: number
}): PaneCloseAction {
  if (options.hasCloser) {
    return 'closer'
  }

  if (options.closeBehavior === 'dismiss') {
    return 'dismiss'
  }

  return options.source?.startsWith('plugin:') && options.sameSourceCount === 1 ? 'disable-plugin' : 'dismiss'
}
