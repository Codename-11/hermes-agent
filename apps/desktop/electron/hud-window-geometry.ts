const HUD_MIN_WIDTH = 380
const HUD_MIN_HEIGHT = 160
const HUD_MAX_WINDOWS_WIDTH = 620 * 2
const HUD_MAX_WINDOWS_HEIGHT = 320 * 2

export interface HudBounds {
  x: number
  y: number
  width: number
  height: number
}

/** Reject invalid persisted bounds and known oversized Windows drag-growth damage. */
export function sanitizeHudState(raw: unknown, platform: NodeJS.Platform): HudBounds | null {
  if (raw == null || typeof raw !== 'object') {
    return null
  }

  const candidate = raw as Partial<HudBounds>

  if (![candidate.x, candidate.y, candidate.width, candidate.height].every(Number.isInteger)) {
    return null
  }

  const bounds = candidate as HudBounds

  if (bounds.width < HUD_MIN_WIDTH || bounds.height < HUD_MIN_HEIGHT) {
    return null
  }

  if (platform === 'win32' && (bounds.width > HUD_MAX_WINDOWS_WIDTH || bounds.height > HUD_MAX_WINDOWS_HEIGHT)) {
    return null
  }

  return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }
}
