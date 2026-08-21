import { beforeEach, describe, expect, it, vi } from 'vitest'

const load = async () => {
  const workspace = await import('./workspace-scope')
  const { Codecs } = await import('./persisted')
  const { $activeGatewayProfile } = await import('@/store/profile')

  return { ...workspace, $activeGatewayProfile, Codecs }
}

beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState({}, '', '/')
  vi.resetModules()
})

describe('workspace-scoped persistence', () => {
  it('pins the boot-adopted profile and ignores later gateway profile switches', async () => {
    window.localStorage.setItem('layout.profile.coder', 'coder-layout')

    const {
      $activeGatewayProfile,
      Codecs,
      activeWorkspaceScope,
      initializeWorkspaceScope,
      workspaceScopedAtom
    } = await load()
    const $layout = workspaceScopedAtom('layout', 'fresh-layout', Codecs.text)

    initializeWorkspaceScope('coder')
    expect(activeWorkspaceScope()).toBe('coder')
    expect($layout.get()).toBe('coder-layout')

    $activeGatewayProfile.set('reviewer')
    initializeWorkspaceScope('reviewer')

    expect(activeWorkspaceScope()).toBe('coder')
    expect($layout.get()).toBe('coder-layout')

    $layout.set('coder-layout-updated')
    expect(window.localStorage.getItem('layout.profile.coder')).toBe('coder-layout-updated')
    expect(window.localStorage.getItem('layout.profile.reviewer')).toBeNull()
  })

  it('derives a helper window scope immediately from its explicit profile override', async () => {
    window.localStorage.setItem('layout.profile.worker', 'worker-layout')
    window.history.replaceState({}, '', '/?profile=worker#/')

    const { Codecs, activeWorkspaceScope, workspaceScopedAtom } = await load()
    const $layout = workspaceScopedAtom('layout', 'fresh-layout', Codecs.text)

    expect(activeWorkspaceScope()).toBe('worker')
    expect($layout.get()).toBe('worker-layout')
  })
})
