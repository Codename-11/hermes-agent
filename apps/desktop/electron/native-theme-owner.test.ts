import { describe, expect, it, vi } from 'vitest'

import { applyNativeThemeFromPrimary } from './native-theme-owner'

function windowWith(webContents: object) {
  return {
    isDestroyed: () => false,
    webContents
  }
}

describe('native theme ownership', () => {
  it('ignores helper renderers while applying and persisting primary renderer updates', () => {
    const primaryContents = {}
    const helperContents = {}
    const primaryWindow = windowWith(primaryContents)
    const nativeTheme = { themeSource: 'dark' }
    const persist = vi.fn()

    applyNativeThemeFromPrimary({ sender: helperContents }, 'light', primaryWindow, nativeTheme, persist)

    expect(nativeTheme.themeSource).toBe('dark')
    expect(persist).not.toHaveBeenCalled()

    applyNativeThemeFromPrimary({ sender: primaryContents }, 'light', primaryWindow, nativeTheme, persist)

    expect(nativeTheme.themeSource).toBe('light')
    expect(persist).toHaveBeenCalledOnce()
    expect(persist).toHaveBeenCalledWith('light')
  })
})
