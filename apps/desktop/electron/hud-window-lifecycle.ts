interface HudCloseEventWindow {
  on(event: 'closed', listener: () => void): unknown
  removeListener(event: 'closed', listener: () => void): unknown
}

const hudCloseBehaviorHandlers = new WeakMap<object, () => void>()

/** Bind replaceable HUD UI behavior without touching unrelated close cleanup listeners. */
export function bindHudCloseBehavior(win: HudCloseEventWindow, handler: () => void): void {
  suppressHudCloseBehavior(win)
  hudCloseBehaviorHandlers.set(win, handler)
  win.on('closed', handler)
}

/** Suppress programmatic-close UI behavior while preserving resource cleanup. */
export function suppressHudCloseBehavior(win: HudCloseEventWindow): void {
  const handler = hudCloseBehaviorHandlers.get(win)

  if (!handler) {
    return
  }

  win.removeListener('closed', handler)
  hudCloseBehaviorHandlers.delete(win)
}
