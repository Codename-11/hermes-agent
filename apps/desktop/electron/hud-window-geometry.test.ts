import { describe, expect, it } from 'vitest'

import { sanitizeHudState } from './hud-window-geometry'

describe('sanitizeHudState', () => {
  it('rejects oversized geometry produced by Windows drag corruption only on Windows', () => {
    const drifted = { x: 965, y: 59, width: 1132, height: 994 }

    expect(sanitizeHudState(drifted, 'win32')).toBeNull()
    expect(sanitizeHudState(drifted, 'darwin')).toEqual(drifted)
  })

  it('accepts compact deliberate Windows geometry and rejects fractional native bounds', () => {
    expect(sanitizeHudState({ x: 100, y: 200, width: 900, height: 600 }, 'win32')).toEqual({
      x: 100,
      y: 200,
      width: 900,
      height: 600
    })
    expect(sanitizeHudState({ x: 100.5, y: 200, width: 620, height: 320 }, 'win32')).toBeNull()
  })
})
