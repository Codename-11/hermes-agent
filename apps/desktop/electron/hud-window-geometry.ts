export const HUD_DEFAULT_WIDTH = 620
export const HUD_DEFAULT_HEIGHT = 320
export const HUD_MIN_WIDTH = 380
export const HUD_MIN_HEIGHT = 160

const HUD_MAX_WIDTH = HUD_DEFAULT_WIDTH * 2
const HUD_MAX_HEIGHT = HUD_DEFAULT_HEIGHT * 2

export interface HudBounds {
  x: number
  y: number
  width: number
  height: number
}

/** Disable the problematic transparent native resize zones only on Windows. */
export function hudNativeWindowOptions(platform: NodeJS.Platform): { resizable: boolean } {
  return { resizable: platform !== 'win32' }
}

export function shouldPinHudDragSize(platform: NodeJS.Platform): boolean {
  return platform === 'win32'
}

/**
 * Validate remembered HUD geometry without reviving Windows drag-growth damage.
 *
 * HUD is deliberately a compact overlay. Native Windows transparent-window
 * resizing can grow it on every move and persist the result; on Windows,
 * anything beyond twice the intended dimensions is treated as corruption and
 * reset to defaults. Other platforms retain their existing resize behavior.
 */
export function sanitizeHudState(raw: unknown, platform: NodeJS.Platform): HudBounds | null {
  if (raw == null || typeof raw !== 'object') {
    return null
  }

  const candidate = raw as Partial<HudBounds>
  const values = [candidate.x, candidate.y, candidate.width, candidate.height]

  if (!values.every(value => Number.isInteger(value))) {
    return null
  }

  const bounds = candidate as HudBounds

  if (
    bounds.width < HUD_MIN_WIDTH ||
    bounds.height < HUD_MIN_HEIGHT ||
    (platform === 'win32' && (bounds.width > HUD_MAX_WIDTH || bounds.height > HUD_MAX_HEIGHT))
  ) {
    return null
  }

  return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }
}

/** Pin a drag to the size captured before Windows starts moving the window. */
export function hudBoundsForDrag(
  position: [number, number],
  delta: { x: number; y: number },
  size: [number, number]
): HudBounds {
  return {
    x: Math.round(position[0] + delta.x),
    y: Math.round(position[1] + delta.y),
    width: Math.round(size[0]),
    height: Math.round(size[1])
  }
}
