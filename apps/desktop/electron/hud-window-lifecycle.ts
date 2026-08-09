interface HudCloseEventWindow {
  on(event: 'closed', listener: () => void): unknown
  removeListener(event: 'closed', listener: () => void): unknown
}

const hudCloseBehaviorHandlers = new WeakMap<object, () => void>()

/**
 * Register the HUD restore/broadcast behavior separately from resource cleanup.
 * Rebinding removes only the previous behavior handler, never unrelated closed
 * listeners installed by cursor polling, stream throttling, or Electron wiring.
 */
export function bindHudCloseBehavior(win: HudCloseEventWindow, handler: () => void): void {
  suppressHudCloseBehavior(win)
  hudCloseBehaviorHandlers.set(win, handler)
  win.on('closed', handler)
}

/** Suppress programmatic-close UI behavior while preserving cleanup listeners. */
export function suppressHudCloseBehavior(win: HudCloseEventWindow): void {
  const handler = hudCloseBehaviorHandlers.get(win)

  if (!handler) {
    return
  }

  win.removeListener('closed', handler)
  hudCloseBehaviorHandlers.delete(win)
}
