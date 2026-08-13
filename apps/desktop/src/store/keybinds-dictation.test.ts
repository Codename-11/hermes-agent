import { beforeEach, describe, expect, it, vi } from 'vitest'

const STORAGE_KEY = 'hermes.desktop.keybinds'

beforeEach(() => {
  window.localStorage.clear()
  vi.resetModules()
})

describe('dictation keybind safety', () => {
  it('migrates an existing bare printable override to the composer-safe default', async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ 'composer.dictate': [';'] }))

    const { bindingsFor } = await import('./keybinds')

    expect(bindingsFor('composer.dictate')).toEqual(['mod+shift+d'])
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}')).toEqual({})
  })

  it('rejects new bare or Shift-only dictation bindings but accepts a primary modifier', async () => {
    const { bindingAllowedForAction } = await import('./keybinds')

    expect(bindingAllowedForAction('composer.dictate', ';')).toBe(false)
    expect(bindingAllowedForAction('composer.dictate', 'shift+d')).toBe(false)
    expect(bindingAllowedForAction('composer.dictate', 'mod+shift+d')).toBe(true)
    expect(bindingAllowedForAction('composer.autoSpeak', ';')).toBe(true)
  })
})
