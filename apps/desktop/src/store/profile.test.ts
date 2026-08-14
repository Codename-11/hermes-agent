import { atom } from 'nanostores'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'
import type { ProfileInfo } from '@/types/hermes'

// Keep profile.ts's side-effecting imports inert: the gateway socket layer and
// the REST query client must not run for real in a unit test.
const ensureGatewayForProfile = vi.fn(async () => undefined)
const openGatewayForProfile = vi.fn(async (_profile: string) => undefined)
const $gateway = atom<unknown>({ id: 'live-socket' })
const resetStarmapGraph = vi.fn()

vi.mock('@/store/gateway', () => ({ $gateway, ensureGatewayForProfile, openGatewayForProfile }))
vi.mock('@/hermes', () => ({
  getProfiles: vi.fn(async () => ({ profiles: [] })),
  setApiRequestProfile: vi.fn()
}))
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph }))

const {
  $activeGatewayProfile,
  $hiddenProfiles,
  $profiles,
  ensureGatewayProfile,
  filterVisibleProfiles,
  prewarmProfileBackend,
  refreshProfiles,
  setProfileHidden,
  switchProfileToSlot
} = await import('./profile')

const { $connection } = await import('./session')
const { invalidateProfileScopedQueries } = await import('@/lib/query-client')
const { getProfiles } = await import('@/hermes')

const profile = (name: string, isDefault = false): ProfileInfo => ({
  has_env: false,
  is_default: isDefault,
  model: null,
  name,
  path: `/tmp/hermes/${name}`,
  provider: null,
  skill_count: 0
})

const remoteConn = (over: Partial<HermesConnection> = {}): HermesConnection =>
  ({ baseUrl: 'https://hermes-roy.tail.ts.net', mode: 'remote', profile: 'vps-remote', ...over }) as HermesConnection

const localConn = (over: Partial<HermesConnection> = {}): HermesConnection =>
  ({ baseUrl: '', mode: 'local', profile: 'default', ...over }) as HermesConnection

const getConnection = vi.fn<(profile?: string | null) => Promise<HermesConnection>>()

beforeEach(() => {
  getConnection.mockReset()
  ensureGatewayForProfile.mockClear()
  openGatewayForProfile.mockClear()
  $gateway.set({ id: 'live-socket' })
  $activeGatewayProfile.set('default')
  $connection.set(localConn())
  $profiles.set([])
  $hiddenProfiles.set([])
  vi.stubGlobal('window', { hermesDesktop: { getConnection } })
  vi.mocked(invalidateProfileScopedQueries).mockClear()
  resetStarmapGraph.mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  $connection.set(null)
})

describe('ensureGatewayProfile → $connection sync (#46651)', () => {
  it('refreshes $connection to the remote descriptor when activating a remote pool profile', async () => {
    // Regression: the primary window backend is local, so $connection.mode is
    // "local". Activating the remote profile must flip it to "remote" — without
    // this, image attach uses path-based image.attach against the remote
    // gateway ("image not found: C:\\…") instead of image.attach_bytes.
    getConnection.mockResolvedValue(remoteConn())

    await ensureGatewayProfile('vps-remote')

    expect(ensureGatewayForProfile).toHaveBeenCalledWith('vps-remote', remoteConn())
    expect(getConnection).toHaveBeenCalledWith('vps-remote')
    expect($connection.get()?.mode).toBe('remote')
    expect($connection.get()?.profile).toBe('vps-remote')
  })

  it('resyncs $connection back to local when returning to the default profile', async () => {
    $activeGatewayProfile.set('vps-remote')
    $connection.set(remoteConn())
    getConnection.mockResolvedValue(localConn())

    await ensureGatewayProfile('default')

    expect(getConnection).toHaveBeenCalledWith('default')
    expect($connection.get()?.mode).toBe('local')
  })

  it('fails closed when the Desktop connection descriptor bridge is unavailable', async () => {
    vi.stubGlobal('window', { hermesDesktop: {} })

    await expect(ensureGatewayProfile('vps-remote')).rejects.toThrow('connection routing is unavailable')

    expect(ensureGatewayForProfile).not.toHaveBeenCalled()
    expect($activeGatewayProfile.get()).toBe('default')
    expect($connection.get()?.mode).toBe('local')
  })

  it('fails before switching the gateway or active profile when the descriptor fetch fails', async () => {
    getConnection.mockRejectedValue(new Error('backend unreachable'))

    await expect(ensureGatewayProfile('vps-remote')).rejects.toThrow('backend unreachable')

    expect(ensureGatewayForProfile).not.toHaveBeenCalled()
    expect($activeGatewayProfile.get()).toBe('default')
    expect($connection.get()?.mode).toBe('local')
  })

  it('resyncs a stale local descriptor when the already-active profile is remote-pinned', async () => {
    $activeGatewayProfile.set('vps-remote')
    $connection.set({ ...localConn(), profile: 'vps-remote' })
    getConnection.mockResolvedValue(remoteConn())

    await ensureGatewayProfile('vps-remote')

    expect(getConnection).toHaveBeenCalledWith('vps-remote')
    expect(ensureGatewayForProfile).not.toHaveBeenCalled()
    expect($connection.get()?.mode).toBe('remote')
  })

  it('publishes the new profile only after its gateway and descriptor are both ready', async () => {
    let resolveDescriptor!: (connection: HermesConnection) => void
    let descriptorRequested!: () => void
    const requested = new Promise<void>(resolve => {
      descriptorRequested = resolve
    })
    const descriptor = new Promise<HermesConnection>(resolve => {
      resolveDescriptor = resolve
    })
    getConnection.mockImplementationOnce(() => {
      descriptorRequested()

      return descriptor
    })

    const switching = ensureGatewayProfile('vps-remote')
    await requested

    expect($activeGatewayProfile.get()).toBe('default')
    expect($connection.get()?.mode).toBe('local')

    resolveDescriptor(remoteConn())
    await switching

    expect($activeGatewayProfile.get()).toBe('vps-remote')
    expect($connection.get()?.mode).toBe('remote')
  })

  it('serializes descriptor resolution across concurrent profile activations', async () => {
    let resolveFirst!: (connection: HermesConnection) => void
    let firstRequested!: () => void
    const requested = new Promise<void>(resolve => {
      firstRequested = resolve
    })
    const firstDescriptor = new Promise<HermesConnection>(resolve => {
      resolveFirst = resolve
    })
    getConnection.mockImplementation(profile => {
      if (profile === 'vps-remote') {
        firstRequested()

        return firstDescriptor
      }

      return Promise.resolve({ ...remoteConn(), profile: 'other-remote' })
    })

    const first = ensureGatewayProfile('vps-remote')
    await requested
    const second = ensureGatewayProfile('other-remote')
    await Promise.resolve()

    expect(getConnection).toHaveBeenCalledTimes(1)
    expect(getConnection).toHaveBeenLastCalledWith('vps-remote')

    resolveFirst(remoteConn())
    await Promise.all([first, second])

    expect(getConnection).toHaveBeenLastCalledWith('other-remote')
    expect($activeGatewayProfile.get()).toBe('other-remote')
    expect($connection.get()?.profile).toBe('other-remote')
  })

  it('serializes multiple activations queued behind one in-flight switch', async () => {
    const requested: string[] = []
    let resolveFirst!: (connection: HermesConnection) => void
    let resolveSecond!: (connection: HermesConnection) => void
    let secondRequested!: () => void
    const secondStarted = new Promise<void>(resolve => {
      secondRequested = resolve
    })
    const firstDescriptor = new Promise<HermesConnection>(resolve => {
      resolveFirst = resolve
    })
    const secondDescriptor = new Promise<HermesConnection>(resolve => {
      resolveSecond = resolve
    })

    getConnection.mockImplementation(profile => {
      requested.push(String(profile))

      if (profile === 'vps-remote') {
        return firstDescriptor
      }

      if (profile === 'queued-b') {
        secondRequested()

        return secondDescriptor
      }

      return Promise.resolve({ ...remoteConn(), profile: 'queued-c' })
    })

    const first = ensureGatewayProfile('vps-remote')
    const second = ensureGatewayProfile('queued-b')
    const third = ensureGatewayProfile('queued-c')

    expect(requested).toEqual(['vps-remote'])

    resolveFirst(remoteConn())
    await secondStarted
    await Promise.resolve()

    expect(requested).toEqual(['vps-remote', 'queued-b'])

    resolveSecond({ ...remoteConn(), profile: 'queued-b' })
    await Promise.all([first, second, third])

    expect(requested).toEqual(['vps-remote', 'queued-b', 'queued-c'])
    expect($activeGatewayProfile.get()).toBe('queued-c')
    expect($connection.get()?.profile).toBe('queued-c')
  })
})

describe('profile-scoped cache invalidation', () => {
  it('drops the memory graph cache when the active gateway profile changes', () => {
    $activeGatewayProfile.set('coder')

    expect(invalidateProfileScopedQueries).toHaveBeenCalled()
    expect(resetStarmapGraph).toHaveBeenCalledTimes(1)
  })
})

describe('prewarmProfileBackend (hover-intent pool spawn)', () => {
  it('opens the gateway (spawn + connect, no activation) for a non-active profile', () => {
    prewarmProfileBackend('warm-basic')

    expect(openGatewayForProfile).toHaveBeenCalledWith('warm-basic')
    // Pre-warm must never activate — that's the click's job.
    expect(ensureGatewayForProfile).not.toHaveBeenCalled()
  })

  it('skips the profile the gateway is already on', () => {
    $activeGatewayProfile.set('warm-active')

    prewarmProfileBackend('warm-active')

    expect(openGatewayForProfile).not.toHaveBeenCalled()
  })

  it('throttles repeat pre-warms for the same profile within the interval', () => {
    prewarmProfileBackend('warm-throttle-a')
    prewarmProfileBackend('warm-throttle-a')
    prewarmProfileBackend('warm-throttle-b')

    const calls = openGatewayForProfile.mock.calls.map(([name]) => name)
    expect(calls.filter(name => name === 'warm-throttle-a')).toHaveLength(1)
    expect(calls.filter(name => name === 'warm-throttle-b')).toHaveLength(1)
  })

  it('swallows spawn failures — error UX belongs to the real switch', () => {
    openGatewayForProfile.mockRejectedValueOnce(new Error('spawn failed'))

    expect(() => prewarmProfileBackend('warm-failing')).not.toThrow()
  })
})

describe('profile rail visibility', () => {
  it('keeps default visible and filters only explicitly hidden named profiles', () => {
    const profiles = [profile('default', true), profile('coder'), profile('ops')]

    setProfileHidden('default', true)
    setProfileHidden('coder', true)

    expect($hiddenProfiles.get()).toEqual(['coder'])
    expect(filterVisibleProfiles(profiles, $hiddenProfiles.get()).map(item => item.name)).toEqual(['default', 'ops'])
  })

  it('skips hidden profiles in positional keyboard navigation', async () => {
    $profiles.set([profile('default', true), profile('coder'), profile('ops')])
    setProfileHidden('coder', true)
    getConnection.mockResolvedValue({ ...localConn(), profile: 'ops' })

    switchProfileToSlot(1)

    await vi.waitFor(() => expect(getConnection).toHaveBeenCalledWith('ops'))
  })
})

describe('refreshProfiles shared rail list (#49289)', () => {
  it('removes a deleted profile from the shared $profiles cache after Manage Profiles refreshes', async () => {
    $profiles.set([profile('default', true), profile('test1')])
    vi.mocked(getProfiles).mockResolvedValueOnce({ profiles: [profile('default', true)] })

    await refreshProfiles()

    expect($profiles.get().map(profile => profile.name)).toEqual(['default'])
  })

  it('leaves the shared $profiles cache intact when the refresh fails', async () => {
    $profiles.set([profile('default', true), profile('test1')])
    vi.mocked(getProfiles).mockRejectedValueOnce(new Error('backend unavailable'))

    await expect(refreshProfiles()).rejects.toThrow('backend unavailable')

    expect($profiles.get().map(profile => profile.name)).toEqual(['default', 'test1'])
  })
})
