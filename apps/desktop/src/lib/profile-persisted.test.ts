import { beforeEach, describe, expect, it, vi } from 'vitest'

const load = async () => {
  const { Codecs } = await import('./persisted')
  const { profilePersistentAtom } = await import('./profile-persisted')
  const { $activeGatewayProfile } = await import('@/store/profile-scope')

  return { $activeGatewayProfile, Codecs, profilePersistentAtom }
}

describe('profilePersistentAtom', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  it('migrates the legacy value into default while named profiles start from fallback', async () => {
    window.localStorage.setItem('legacy', 'legacy-default')

    const { $activeGatewayProfile, Codecs, profilePersistentAtom } = await load()

    const $value = profilePersistentAtom({
      codec: Codecs.text,
      fallback: () => 'fresh',
      key: 'profiles',
      legacyKey: 'legacy'
    })

    expect($value.get()).toBe('legacy-default')

    $activeGatewayProfile.set('worker')
    expect($value.get()).toBe('fresh')

    $value.set('worker-value')
    $activeGatewayProfile.set('default')
    expect($value.get()).toBe('legacy-default')

    const encoded = JSON.parse(window.localStorage.getItem('profiles') ?? '{}') as Record<string, string>

    expect(encoded).toEqual({ default: 'legacy-default', worker: 'worker-value' })
  })

  it('restores each profile after a full module reload', async () => {
    const first = await load()

    const $first = first.profilePersistentAtom({
      codec: first.Codecs.text,
      fallback: () => 'fresh',
      key: 'profiles'
    })

    $first.set('default-value')
    first.$activeGatewayProfile.set('worker')
    $first.set('worker-value')

    vi.resetModules()

    const second = await load()

    const $second = second.profilePersistentAtom({
      codec: second.Codecs.text,
      fallback: () => 'fresh',
      key: 'profiles'
    })

    expect($second.get()).toBe('default-value')
    second.$activeGatewayProfile.set('worker')
    expect($second.get()).toBe('worker-value')
    second.$activeGatewayProfile.set('default')
    expect($second.get()).toBe('default-value')
  })

  it('adds a missing default migration even when named profile data already exists', async () => {
    window.localStorage.setItem('legacy', 'legacy-default')
    window.localStorage.setItem('profiles', JSON.stringify({ worker: 'worker-value' }))

    const { Codecs, profilePersistentAtom } = await load()

    const $value = profilePersistentAtom({
      codec: Codecs.text,
      fallback: () => 'fresh',
      key: 'profiles',
      legacyKey: 'legacy'
    })

    expect($value.get()).toBe('legacy-default')
    expect($value.getForProfile('worker')).toBe('worker-value')
  })

  it('supports explicit flush without persisting hot in-memory updates', async () => {
    const { Codecs, profilePersistentAtom } = await load()

    const $value = profilePersistentAtom({
      autoPersist: false,
      codec: Codecs.text,
      fallback: () => 'fresh',
      key: 'profiles'
    })

    $value.set('drag-frame')
    expect(window.localStorage.getItem('profiles')).toBeNull()

    $value.persistCurrent()
    expect(window.localStorage.getItem('profiles')).toContain('drag-frame')
  })
})
