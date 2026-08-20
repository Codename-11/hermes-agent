import { atom } from 'nanostores'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'

import { deferred } from '../test/deferred'

// Registry-agent activation (ensureGatewayAgent — the SDK ensureAgent door).
// Two regressions pinned here:
//  1. Activating an ALREADY-OPEN registry agent must still resync
//     $connection (via getConnectionFor) and move $activeGatewayProfile —
//     previously only a freshly-dialed socket synced $connection (inside
//     openSecondary), so re-activating an open agent left REST/fs/media and
//     image-attach routing on the previous backend (same class as #46651).
//  2. Agent activations share the gatewaySwitch mutex with profile switches —
//     without it, two rapid activations could complete out of order and the
//     EARLIER setActive() landed last.

const INITIAL_GATEWAY = { id: 'live-socket' }
const AGENT_GATEWAY = { id: 'agent-socket' }
const PROFILE_GATEWAY = { id: 'profile-socket' }

const activateAgent = vi.fn(() => {
  $gateway.set(AGENT_GATEWAY)

  return true
})

const activateProfile = vi.fn(() => {
  $gateway.set(PROFILE_GATEWAY)

  return true
})

const prepareGatewayForAgent = vi.fn(
  async (
    _connectionId: null | string,
    _profile: string,
    _resolvedConnection?: HermesConnection | null
  ): Promise<() => boolean> => activateAgent
)

const prepareGatewayForProfile = vi.fn(
  async (_profile: string, _resolvedConnection?: HermesConnection | null): Promise<() => boolean> => activateProfile
)

const openGatewayForProfile = vi.fn(async (_profile: string) => undefined)
const $gateway = atom<unknown>(INITIAL_GATEWAY)
const resetStarmapGraph = vi.fn()

vi.mock('@/store/gateway', () => ({
  $gateway,
  openGatewayForProfile,
  prepareGatewayForAgent,
  prepareGatewayForProfile
}))
vi.mock('@/hermes', () => ({
  getProfiles: vi.fn(async () => ({ profiles: [] })),
  setApiRequestProfile: vi.fn()
}))
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph }))

const { $activeGatewayProfile, ensureGatewayAgent, ensureGatewayProfile } = await import('./profile')
const { $connection } = await import('./session')

const agentConn = (over: Partial<HermesConnection> = {}): HermesConnection =>
  ({ baseUrl: 'https://homelab.invalid', mode: 'remote', profile: 'research', ...over }) as HermesConnection

const localConn = (over: Partial<HermesConnection> = {}): HermesConnection =>
  ({ baseUrl: '', mode: 'local', profile: 'default', ...over }) as HermesConnection

const getConnection = vi.fn<(profile?: string | null) => Promise<HermesConnection>>()

const getConnectionFor =
  vi.fn<(payload: { connectionId?: null | string; profile?: null | string }) => Promise<HermesConnection>>()

beforeEach(() => {
  getConnection.mockReset()
  getConnectionFor.mockReset()
  prepareGatewayForAgent.mockReset()
  prepareGatewayForAgent.mockResolvedValue(activateAgent)
  prepareGatewayForProfile.mockReset()
  prepareGatewayForProfile.mockResolvedValue(activateProfile)
  activateAgent.mockClear()
  activateProfile.mockClear()
  $gateway.set(INITIAL_GATEWAY)
  $activeGatewayProfile.set('default')
  $connection.set(localConn())
  vi.stubGlobal('window', { hermesDesktop: { getConnection, getConnectionFor } })
})

afterEach(() => {
  vi.unstubAllGlobals()
  $connection.set(null)
})

describe('ensureGatewayAgent → $connection / $activeGatewayProfile sync', () => {
  it('resyncs $connection and $activeGatewayProfile even when the agent socket is already open', async () => {
    // The store-level activation resolves instantly (socket already open) —
    // exactly the case that used to skip the sync entirely.
    const descriptor = agentConn()
    getConnectionFor.mockResolvedValue(descriptor)

    await ensureGatewayAgent('homelab', 'research')

    expect(prepareGatewayForAgent).toHaveBeenCalledWith('homelab', 'research', descriptor)
    expect(prepareGatewayForAgent.mock.calls[0]?.[2]).toBe(descriptor)
    expect(activateAgent).toHaveBeenCalledOnce()
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'homelab', profile: 'research' })
    expect($activeGatewayProfile.get()).toBe('research')
    expect($connection.get()?.mode).toBe('remote')
    expect($connection.get()?.profile).toBe('research')
  })

  it('leaves the prior connection intact when the descriptor fetch fails', async () => {
    getConnectionFor.mockRejectedValue(new Error('source unreachable'))

    await ensureGatewayAgent('homelab', 'research')

    expect(prepareGatewayForAgent).toHaveBeenCalledWith('homelab', 'research', null)
    expect(activateAgent).toHaveBeenCalledOnce()
    expect($activeGatewayProfile.get()).toBe('research')
    // Best-effort: boot/reconnect resyncs later; we must not null it out here.
    expect($connection.get()?.mode).toBe('local')
  })

  it('does not republish a registry identity invalidated during activation', async () => {
    prepareGatewayForAgent.mockResolvedValueOnce(() => false)

    await ensureGatewayAgent('removed-source', 'research')

    expect($activeGatewayProfile.get()).toBe('default')
    expect($connection.get()?.mode).toBe('local')
    expect($gateway.get()).toBe(INITIAL_GATEWAY)
    expect(getConnectionFor).toHaveBeenCalledTimes(1)
  })

  it('falls through to the prepared profile seam for a null connectionId', async () => {
    const descriptor = agentConn({ mode: 'local', profile: 'research' })
    getConnection.mockResolvedValue(descriptor)

    await ensureGatewayAgent(null, 'research')

    expect(prepareGatewayForProfile).toHaveBeenCalledWith('research', descriptor)
    expect(prepareGatewayForAgent).not.toHaveBeenCalled()
    expect(getConnectionFor).not.toHaveBeenCalled()
  })

  it('keeps an explicit local registry id on the registry-aware path', async () => {
    const descriptor = localConn({ profile: 'research' })
    getConnectionFor.mockResolvedValue(descriptor)

    await ensureGatewayAgent('local', 'research')

    expect(prepareGatewayForAgent).toHaveBeenCalledWith('local', 'research', descriptor)
    expect(prepareGatewayForProfile).not.toHaveBeenCalled()
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'local', profile: 'research' })
  })

  it('publishes the registry gateway, profile, and descriptor as one observable tuple', async () => {
    const descriptor = agentConn()
    getConnectionFor.mockResolvedValue(descriptor)
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
      await ensureGatewayAgent('homelab', 'research')
    } finally {
      stops.forEach(stop => stop())
    }

    expect(seen).toHaveLength(3)
    expect(seen).toEqual(Array(3).fill({ connection: 'research', gateway: AGENT_GATEWAY, profile: 'research' }))
  })
})

describe('ensureGatewayAgent shares the gatewaySwitch mutex with profile switches', () => {
  it('serializes an agent activation behind an in-flight profile switch', async () => {
    const profileGate = deferred()
    const order: string[] = []

    prepareGatewayForProfile.mockImplementation(async (profile: string) => {
      order.push(`profile:${profile}`)
      await profileGate.promise

      return activateProfile
    })
    prepareGatewayForAgent.mockImplementation(async (_connectionId, profile) => {
      order.push(`agent:${profile}`)

      return activateAgent
    })
    getConnection.mockResolvedValue(localConn({ profile: 'worker' }))
    getConnectionFor.mockResolvedValue(agentConn())

    // Start a profile switch that stalls mid-flight, then an agent
    // activation. The agent activation must NOT start until the profile
    // switch settles — otherwise the earlier setActive could land last.
    const profileSwitch = ensureGatewayProfile('worker')
    await Promise.resolve()
    const agentSwitch = ensureGatewayAgent('homelab', 'research')
    await Promise.resolve()

    expect(order).toEqual(['profile:worker'])

    profileGate.resolve()
    await profileSwitch
    await agentSwitch

    expect(order).toEqual(['profile:worker', 'agent:research'])
    // The LAST activation wins the active pointer.
    expect($activeGatewayProfile.get()).toBe('research')
    expect($connection.get()?.profile).toBe('research')
  })

  it('serializes a profile switch behind an in-flight agent activation', async () => {
    const agentGate = deferred()
    const order: string[] = []

    prepareGatewayForAgent.mockImplementation(async (_connectionId, profile) => {
      order.push(`agent:${profile}`)
      await agentGate.promise

      return activateAgent
    })
    prepareGatewayForProfile.mockImplementation(async (profile: string) => {
      order.push(`profile:${profile}`)

      return activateProfile
    })
    getConnection.mockResolvedValue(localConn({ profile: 'worker' }))
    getConnectionFor.mockResolvedValue(agentConn())

    const agentSwitch = ensureGatewayAgent('homelab', 'research')
    await Promise.resolve()
    const profileSwitch = ensureGatewayProfile('worker')
    await Promise.resolve()

    expect(order).toEqual(['agent:research'])

    agentGate.resolve()
    await agentSwitch
    await profileSwitch

    expect(order).toEqual(['agent:research', 'profile:worker'])
    expect($activeGatewayProfile.get()).toBe('worker')
  })
})
