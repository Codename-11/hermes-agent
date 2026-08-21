/**
 * Cursor→page coordinate math for the HUD's Windows/Linux click-through fallback.
 *
 * The HUD decides whether to swallow the mouse by hit-testing the document
 * under the cursor, which needs a cursor position in CSS pixels. On macOS that
 * arrives for free: `setIgnoreMouseEvents(true, { forward: true })`
 * keeps mousemove flowing to the page even while the window is ignoring, so the
 * renderer can always see where the pointer is and re-arm when it returns to
 * the bar. Linux does not support forwarding, while transparent frameless
 * Windows windows can remain click-through despite it. In either case moves stop
 * before the renderer can re-arm the composer.
 *
 * Main can still see the cursor, so on Windows/Linux it polls and pushes the position
 * in. This is the conversion that push needs, kept pure and separate because
 * the two unit mismatches in it are exactly what silently makes a hit test miss
 * by a hand's width.
 */

interface Point {
  x: number
  y: number
}

interface Bounds {
  x: number
  y: number
  width: number
  height: number
}

/** Poll native cursor state where transparent click-through forwarding cannot reliably re-arm the HUD. */
export function shouldFeedHudCursor(platform: NodeJS.Platform): boolean {
  return platform === 'linux' || platform === 'win32'
}

/**
 * Screen-space cursor → the window's CSS pixel coordinates, or null when the
 * cursor is outside the window.
 *
 * Two conversions, both load-bearing:
 *
 *   - Screen to window: Electron reports the cursor in screen space and the
 *     window has its own origin, so the window's position has to come off
 *     first.
 *   - DIP to CSS: `getCursorScreenPoint()` and `getBounds()` both speak
 *     device-independent pixels, and `elementFromPoint` speaks CSS pixels. Those
 *     are the same number only at zoom 1. Anyone who has pressed ⌘− is reading
 *     a smaller page than the window is wide, and an unscaled point lands
 *     progressively further from the cursor the further it is from the origin.
 *
 * Returning null outside the window matters as much as the arithmetic: it is
 * the renderer's signal that it has lost the cursor, which is what hands the
 * window back to the app underneath instead of leaving it solid on a stale
 * point.
 */
export function cursorPointInWindow(cursor: Point, bounds: Bounds, zoomFactor: number): Point | null {
  const dx = cursor.x - bounds.x
  const dy = cursor.y - bounds.y

  if (dx < 0 || dy < 0 || dx >= bounds.width || dy >= bounds.height) {
    return null
  }

  // A zero or bogus factor would divide the point into infinity; treat anything
  // non-positive as unzoomed rather than poisoning the hit test.
  const scale = Number.isFinite(zoomFactor) && zoomFactor > 0 ? zoomFactor : 1

  return { x: dx / scale, y: dy / scale }
}
