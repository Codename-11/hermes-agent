import { atom } from 'nanostores'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'
import type { ProfileInfo } from '@/types/hermes'

// Keep profile.ts's side-effecting imports inert: the gateway socket layer and
// the REST query client must not run for real in a unit test.
const TARGET_GATEWAY = { id: 'target-socket' }

const activateGateway = vi.fn(() => {
  $gateway.set(TARGET_GATEWAY)

  return true
})

const prepareGatewayForProfile = vi.fn(
  async (_profile: string, _resolvedConnection?: HermesConnection | null): Promise<() => boolean> => activateGateway
)

const openGatewayForProfile = vi.fn(async (_profile: string) => undefined)
const openGatewayForAgent = vi.fn(async (_connectionId: null | string, _profile: string) => undefined)

const prepareGatewayForAgent = vi.fn(
  async (
    _connectionId: null | string,
    _profile: string,
    _resolvedConnection?: HermesConnection | null
  ): Promise<() => boolean> => activateGateway
)

const $gateway = atom<unknown>({ id: 'live-socket' })
const resetStarmapGraph = vi.fn()

vi.mock('@/store/gateway', () => ({
  $gateway,
  openGatewayForAgent,
  openGatewayForProfile,
  prepareGatewayForAgent,
  prepareGatewayForProfile
}))
vi.mock('@/hermes', () => ({
  getApiRequestConnection: vi.fn<() => null | string>(() => null),
  getProfiles: vi.fn(async () => ({ profiles: [] })),
  setApiRequestProfile: vi.fn()
}))
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph }))

const {
  $activeGatewayProfile,
  $browsedProfile,
  $profiles,
  $showAllProfiles,
  cycleProfile,
  ensureGatewayProfile,
  invalidateProfileListFetches,
  newSessionInProfile,
  prewarmProfileBackend,
  refreshProfiles,
  selectProfile
} = await import('./profile')

const { $connection } = await import('./session')
const { invalidateProfileScopedQueries } = await import('@/lib/query-client')
const { getApiRequestConnection, getProfiles } = await import('@/hermes')

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

const getConnectionFor = vi.fn<
  (payload: { connectionId?: null | string; profile?: null | string }) => Promise<HermesConnection>
>()

beforeEach(() => {
  getConnection.mockReset()
  getConnectionFor.mockReset()
  prepareGatewayForProfile.mockReset()
  prepareGatewayForProfile.mockResolvedValue(activateGateway)
  prepareGatewayForAgent.mockReset()
  prepareGatewayForAgent.mockResolvedValue(activateGateway)
  activateGateway.mockClear()
  openGatewayForProfile.mockClear()
  openGatewayForAgent.mockClear()
  $gateway.set({ id: 'live-socket' })
  $activeGatewayProfile.set('default')
  $browsedProfile.set('default')
  $showAllProfiles.set(false)
  $connection.set(localConn())
  $profiles.set([])
  vi.stubGlobal('window', { hermesDesktop: { getConnection, getConnectionFor } })
  vi.mocked(getApiRequestConnection).mockReturnValue(null)
  vi.mocked(invalidateProfileScopedQueries).mockClear()
  resetStarmapGraph.mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  $connection.set(null)
})

describe('profile rail route preservation (#88680)', () => {
  it('keeps a named profile on the active registered remote source', async () => {
    const descriptor = remoteConn({ connectionId: 'homelab', profile: 'mizu', registryScoped: true })

    $connection.set(remoteConn({ connectionId: 'homelab', profile: 'default', registryScoped: true }))
    getConnectionFor.mockResolvedValue(descriptor)

    selectProfile('mizu')

    await vi.waitFor(() => expect(prepareGatewayForAgent).toHaveBeenCalledOnce())
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'homelab', profile: 'mizu' })
    expect(prepareGatewayForAgent).toHaveBeenCalledWith('homelab', 'mizu', descriptor)
    expect(getConnection).not.toHaveBeenCalled()
    expect($connection.get()?.connectionId).toBe('homelab')
  })

  it('prewarms and starts fresh chats on the active registered source', async () => {
    const descriptor = remoteConn({ connectionId: 'homelab', profile: 'mizu', registryScoped: true })

    $connection.set(remoteConn({ connectionId: 'homelab', profile: 'default', registryScoped: true }))
    getConnectionFor.mockResolvedValue(descriptor)

    prewarmProfileBackend('mizu')
    newSessionInProfile('mizu')

    expect(openGatewayForAgent).toHaveBeenCalledWith('homelab', 'mizu')
    await vi.waitFor(() => expect(prepareGatewayForAgent).toHaveBeenCalledOnce())
    expect(getConnection).not.toHaveBeenCalled()
  })

  it('prefers the active gateway source over a stale connection descriptor when returning to default', async () => {
    const descriptor = remoteConn({ connectionId: 'homelab', profile: 'default', registryScoped: true })

    vi.mocked(getApiRequestConnection).mockReturnValue('homelab')
    $connection.set(localConn({ connectionId: 'local', profile: 'mizu', registryScoped: true }))
    getConnectionFor.mockResolvedValue(descriptor)

    selectProfile('default')

    await vi.waitFor(() => expect(prepareGatewayForAgent).toHaveBeenCalledOnce())
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'homelab', profile: 'default' })
    expect(prepareGatewayForAgent).toHaveBeenCalledWith('homelab', 'default', descriptor)
  })

  it('retains the legacy profile route when no registry source is active', async () => {
    const descriptor = localConn({ profile: 'mizu' })

    getConnection.mockResolvedValue(descriptor)

    selectProfile('mizu')

    await vi.waitFor(() => expect(prepareGatewayForProfile).toHaveBeenCalledOnce())
    expect(getConnection).toHaveBeenCalledWith('mizu')
    expect(getConnectionFor).not.toHaveBeenCalled()
  })
})

describe('ensureGatewayProfile → $connection sync (#46651)', () => {
  it('refreshes $connection to the remote descriptor when activating a remote pool profile', async () => {
    // Regression: the primary window backend is local, so $connection.mode is
    // "local". Activating the remote profile must flip it to "remote" — without
    // this, image attach uses path-based image.attach against the remote
    // gateway ("image not found: C:\\…") instead of image.attach_bytes.
    const descriptor = remoteConn()
    getConnection.mockResolvedValue(descriptor)

    await ensureGatewayProfile('vps-remote')

    expect(prepareGatewayForProfile).toHaveBeenCalledWith('vps-remote', descriptor)
    expect(prepareGatewayForProfile.mock.calls[0]?.[1]).toBe(descriptor)
    expect(activateGateway).toHaveBeenCalledOnce()
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

  it('leaves the prior connection intact when the descriptor fetch fails', async () => {
    getConnection.mockRejectedValue(new Error('backend unreachable'))

    await ensureGatewayProfile('vps-remote')

    // Best-effort: boot/reconnect resyncs later; we must not null it out here.
    expect(prepareGatewayForProfile).toHaveBeenCalledWith('vps-remote', null)
    expect(activateGateway).toHaveBeenCalledOnce()
    expect($activeGatewayProfile.get()).toBe('vps-remote')
    expect($connection.get()?.mode).toBe('local')
  })

  it('does not churn $connection when the target is already the active profile', async () => {
    $activeGatewayProfile.set('vps-remote')
    $connection.set(remoteConn())

    await ensureGatewayProfile('vps-remote')

    expect(getConnection).not.toHaveBeenCalled()
    expect(prepareGatewayForProfile).not.toHaveBeenCalled()
    expect($connection.get()?.mode).toBe('remote')
  })
})

describe('profile-scoped cache invalidation', () => {
  it('drops the memory graph cache when the active gateway profile changes', () => {
    $activeGatewayProfile.set('coder')

    expect(invalidateProfileScopedQueries).toHaveBeenCalled()
    expect(resetStarmapGraph).toHaveBeenCalledTimes(1)
  })
})

describe('profile activation publication', () => {
  it('publishes gateway, profile, and connection as one observable tuple', async () => {
    const descriptor = remoteConn()
    getConnection.mockResolvedValue(descriptor)
    const seen: Array<{ connection?: string; gateway: unknown; profile: string }> = []

    const observe = () =>
      seen.push({
        connection: $connection.get()?.profile,
        gateway: $gateway.get(),
        profile: $activeGatewayProfile.get()
      })

    const stops = [
      $gateway.listen(() => observe()),
      $activeGatewayProfile.listen(() => observe()),
      $connection.listen(() => observe())
    ]

    try {
      await ensureGatewayProfile('vps-remote')
    } finally {
      stops.forEach(stop => stop())
    }

    expect(seen).toHaveLength(3)
    expect(seen).toEqual(
      Array(3).fill({ connection: 'vps-remote', gateway: TARGET_GATEWAY, profile: 'vps-remote' })
    )
  })

  it('publishes nothing when the prepared activation is superseded', async () => {
    const descriptor = remoteConn()
    getConnection.mockResolvedValue(descriptor)
    prepareGatewayForProfile.mockResolvedValueOnce(() => false)
    const seen: unknown[] = []
    const stop = $gateway.listen(gateway => seen.push(gateway))

    try {
      await ensureGatewayProfile('vps-remote')
    } finally {
      stop()
    }

    expect(seen).toEqual([])
    expect($activeGatewayProfile.get()).toBe('default')
    expect($connection.get()?.profile).toBe('default')
  })
})

describe('profile rail navigation', () => {
  it('cycles from the browsed profile when a shared remote gateway remains on the primary profile', () => {
    $profiles.set([profile('default', true), profile('mizu'), profile('victor')])
    $activeGatewayProfile.set('victor')
    $browsedProfile.set('mizu')

    cycleProfile(1)

    expect($browsedProfile.get()).toBe('victor')
  })
})

describe('prewarmProfileBackend (hover-intent pool spawn)', () => {
  it('opens the gateway (spawn + connect, no activation) for a non-active profile', () => {
    prewarmProfileBackend('warm-basic')

    expect(openGatewayForProfile).toHaveBeenCalledWith('warm-basic')
    // Pre-warm must never activate — that's the click's job.
    expect(prepareGatewayForProfile).not.toHaveBeenCalled()
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

describe('stale profile-list fetches across a backend switch (#85731)', () => {
  it('a late response from the previous backend cannot clobber the new backend list', async () => {
    // The disappearing-rail mechanism: /api/profiles is in flight against
    // backend A when the user applies a different remote/Cloud connection.
    // The soft re-home fetches backend B's list, then A's late (often empty /
    // default-only) response lands LAST and collapses the rail.
    let resolveOld: (value: { profiles: ProfileInfo[] }) => void = () => undefined
    vi.mocked(getProfiles).mockImplementationOnce(() => new Promise(resolve => (resolveOld = resolve)))

    const oldFetch = refreshProfiles() // in flight against backend A

    // Connection apply → soft re-home strands in-flight fetches...
    invalidateProfileListFetches()

    // ...and the new backend's list arrives.
    vi.mocked(getProfiles).mockResolvedValueOnce({
      profiles: [profile('default', true), profile('eric'), profile('coder')]
    })
    await refreshProfiles()
    expect($profiles.get().map(profile => profile.name)).toEqual(['default', 'eric', 'coder'])

    // Backend A's stale response finally lands: it must NOT overwrite $profiles.
    resolveOld({ profiles: [profile('default', true)] })
    await oldFetch

    expect($profiles.get().map(profile => profile.name)).toEqual(['default', 'eric', 'coder'])
  })

  it('a normal refresh still writes the cache after prior invalidations', async () => {
    invalidateProfileListFetches()
    vi.mocked(getProfiles).mockResolvedValueOnce({ profiles: [profile('default', true), profile('solo')] })

    await refreshProfiles()

    expect($profiles.get().map(profile => profile.name)).toEqual(['default', 'solo'])
  })

  it('strands in-flight fetches when the active gateway profile swaps backends', async () => {
    // Sibling site of the same class: a live profile swap moves /api/profiles
    // routing to another backend mid-fetch. The $activeGatewayProfile
    // subscriber must bump the epoch exactly like the connection-apply wipe.
    let resolveOld: (value: { profiles: ProfileInfo[] }) => void = () => undefined
    vi.mocked(getProfiles).mockImplementationOnce(() => new Promise(resolve => (resolveOld = resolve)))

    const oldFetch = refreshProfiles() // in flight against the old profile's backend

    $activeGatewayProfile.set('coder') // swap → subscriber invalidates

    vi.mocked(getProfiles).mockResolvedValueOnce({
      profiles: [profile('default', true), profile('coder')]
    })
    await refreshProfiles()

    resolveOld({ profiles: [] })
    await oldFetch

    expect($profiles.get().map(profile => profile.name)).toEqual(['default', 'coder'])
  })
})
