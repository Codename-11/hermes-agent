import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $activeGatewayProfile, $gatewayProfileAdopted } from '@/store/profile-scope'

import { __resetBackendSkinSync, ingestBackendSkin } from './backend-sync'
import { modePref, ThemeProvider } from './context'

// The live-authoring loop: Hermes writes/edits one skin file and every surface
// repaints. An in-place edit keeps the NAME — only the palette moves.
const bloomberg = (foreground: string) => ({
  name: 'bloomberg',
  colors: { background: '#000000', ui_text: foreground, ui_accent: '#ff8000' }
})

const cssVar = (name: string) => window.document.documentElement.style.getPropertyValue(name)
const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }
const initialHermesDesktop = desktopWindow.hermesDesktop
const setNativeTheme = vi.fn()

describe('ThemeProvider ← backend skin sync', () => {
  beforeEach(() => {
    window.localStorage.clear()
    __resetBackendSkinSync()
    $activeGatewayProfile.set('default')
    $gatewayProfileAdopted.set(false)
    setNativeTheme.mockClear()
    desktopWindow.hermesDesktop = { setNativeTheme } as unknown as Window['hermesDesktop']
  })

  afterEach(() => {
    cleanup()
    $activeGatewayProfile.set('default')
    $gatewayProfileAdopted.set(false)

    if (initialHermesDesktop) {
      desktopWindow.hermesDesktop = initialHermesDesktop
    } else {
      delete desktopWindow.hermesDesktop
    }
  })

  it('applies an activated backend skin', () => {
    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>
    )

    act(() => ingestBackendSkin(bloomberg('#ff9f0a'), { apply: true }))

    expect(cssVar('--theme-foreground')).toBe('#ff9f0a')
    expect(cssVar('--theme-background-seed')).toBe('#000000')
  })

  it('repaints an in-place edit of the ACTIVE skin (same name, new palette)', () => {
    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>
    )

    act(() => ingestBackendSkin(bloomberg('#ff9f0a'), { apply: true }))
    expect(cssVar('--theme-foreground')).toBe('#ff9f0a')

    // Recolor the same skin file. The same-name apply guard correctly no-ops
    // (protects manual desktop picks), so the repaint must come from the
    // registry update reaching the active theme derivation.
    act(() => ingestBackendSkin(bloomberg('#ff2d95'), { apply: true }))
    expect(cssVar('--theme-foreground')).toBe('#ff2d95')
  })

  it('does not repaint an edit to an INACTIVE skin', () => {
    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>
    )

    act(() => ingestBackendSkin(bloomberg('#ff9f0a'), { apply: true }))

    // A different skin registered without apply (e.g. seeded on reconnect)
    // must not touch the painted theme.
    act(() =>
      ingestBackendSkin({ name: 'forest', colors: { background: '#001100', ui_text: '#66ff66' } }, { apply: false })
    )
    expect(cssVar('--theme-foreground')).toBe('#ff9f0a')
  })

  it('restores a saved backend skin after its cold-start registry seed arrives', async () => {
    window.localStorage.setItem('hermes-desktop-profile-themes-v1', JSON.stringify({ default: 'bloomberg' }))

    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>
    )

    // The backend theme is unavailable during the synchronous boot paint, so
    // Nous may paint provisionally. A gateway.ready seed must hydrate the saved
    // identity instead of leaving the app stranded on the blue fallback.
    act(() => ingestBackendSkin(bloomberg('#ff9f0a'), { apply: false }))

    await waitFor(() => {
      expect(cssVar('--theme-foreground')).toBe('#ff9f0a')
      expect(cssVar('--theme-background-seed')).toBe('#000000')
    })
  })

  it('reconciles the native theme after adopting the authoritative profile', async () => {
    window.localStorage.setItem('hermes-desktop-active-profile-v1', 'boot')
    modePref.assign('boot', 'light')
    modePref.assign('work', 'dark')

    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>
    )

    await waitFor(() => expect(setNativeTheme).toHaveBeenLastCalledWith('light'))
    setNativeTheme.mockClear()

    act(() => {
      $activeGatewayProfile.set('work')
      $gatewayProfileAdopted.set(true)
    })

    await waitFor(() => expect(setNativeTheme).toHaveBeenLastCalledWith('dark'))
  })
})
