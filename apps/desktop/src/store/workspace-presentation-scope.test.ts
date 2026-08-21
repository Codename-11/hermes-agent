import { beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState({}, '', '/')
  vi.resetModules()
})

describe('workspace-scoped presentation state', () => {
  it('keeps pane geometry on the boot workspace across a live gateway profile switch', async () => {
    window.localStorage.setItem(
      'hermes.desktop.paneStates.v1.profile.coder',
      JSON.stringify({ 'chat-sidebar': { open: true, widthOverride: 280 } })
    )

    const { initializeWorkspaceScope } = await import('@/lib/workspace-scope')
    initializeWorkspaceScope('coder')

    const { $activeGatewayProfile } = await import('./profile')
    const { $paneStates, setPaneWidthOverride } = await import('./panes')

    expect($paneStates.get()['chat-sidebar']?.widthOverride).toBe(280)

    $activeGatewayProfile.set('reviewer')
    setPaneWidthOverride('chat-sidebar', 310)

    expect($paneStates.get()['chat-sidebar']?.widthOverride).toBe(310)
    expect(JSON.parse(window.localStorage.getItem('hermes.desktop.paneStates.v1.profile.coder') ?? '{}')).toEqual({
      'chat-sidebar': { open: true, widthOverride: 310 }
    })
    expect(window.localStorage.getItem('hermes.desktop.paneStates.v1.profile.reviewer')).toBeNull()
  })
})
