import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'

const gatewayMocks = vi.hoisted(() => ({
  connect: vi.fn(async (_wsUrl: string): Promise<void> => undefined)
}))

vi.mock('@/hermes', () => ({
  getApiRequestConnection: vi.fn(() => 'homelab'),
  getProfiles: vi.fn(async () => ({ profiles: [] })),
  hermesApi: vi.fn(),
  HermesGateway: class {
    connectionState = 'closed'
    private readonly stateListeners = new Set<(state: string) => void>()

    private emitState(state: string): void {
      this.connectionState = state
      this.stateListeners.forEach(listener => listener(state))
    }

    connect = async (wsUrl: string): Promise<void> => {
      this.emitState('connecting')
      await gatewayMocks.connect(wsUrl)
      this.emitState('open')
    }
    close = (): void => {
      this.emitState('closed')
    }
    onEvent = vi.fn(() => () => {})
    onState = vi.fn((listener: (state: string) => void) => {
      this.stateListeners.add(listener)

      return () => this.stateListeners.delete(listener)
    })
  },
  setApiRequestConnection: vi.fn(),
  setApiRequestProfile: vi.fn(),
  STARTUP_REQUEST_TIMEOUT_MS: 30_000
}))
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/store/cron-model-impact-scope', () => ({
  invalidateCronModelImpactScopeState: vi.fn(),
  syncCronModelImpactConnection: vi.fn()
}))
vi.mock('@/store/live-sync', () => ({ activateChangeEventsProfile: vi.fn() }))
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph: vi.fn() }))

const { $activeGatewayProfile, ensureGatewayAgent, selectProfile } = await import('./profile')
const { activeGateway, closeSecondaryGateways, configureGatewayRegistry, setPrimaryGateway } = await import('./gateway')
const { $connection } = await import('./session')

const descriptor = {
  authMode: 'token',
  baseUrl: 'https://homelab.invalid',
  connectionId: 'homelab',
  mode: 'remote',
  profile: 'research',
  registryScoped: true,
  token: 'single-ticket',
  wsUrl: 'wss://homelab.invalid/api/ws?token=single-ticket'
} as HermesConnection

const getConnectionFor = vi.fn(async () => descriptor)

beforeEach(() => {
  closeSecondaryGateways()
  configureGatewayRegistry({ onEvent: vi.fn() })
  setPrimaryGateway({ connectionState: 'open' } as never, 'default')
  $activeGatewayProfile.set('default')
  $connection.set(null)
  getConnectionFor.mockClear()
  gatewayMocks.connect.mockClear()
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    getConnection: vi.fn(async () => descriptor),
    getConnectionFor
  }
})

afterEach(() => {
  closeSecondaryGateways()
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  $connection.set(null)
})

describe('profile caller composed with the real gateway registry', () => {
  it('uses one registry lookup and dials the exact descriptor through activation', async () => {
    await ensureGatewayAgent('homelab', 'research')

    expect(getConnectionFor).toHaveBeenCalledOnce()
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'homelab', profile: 'research' })
    expect(gatewayMocks.connect).toHaveBeenCalledOnce()
    expect(gatewayMocks.connect).toHaveBeenCalledWith(descriptor.wsUrl)
    expect(activeGateway()).not.toBeNull()
    expect($activeGatewayProfile.get()).toBe('research')
    expect($connection.get()).toBe(descriptor)
  })

  it('keeps a profile-rail selection on the active registry source', async () => {
    $connection.set({ ...descriptor, profile: 'default' })
    selectProfile('research')

    await vi.waitFor(() => expect(gatewayMocks.connect).toHaveBeenCalledOnce())
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'homelab', profile: 'research' })
    expect(gatewayMocks.connect).toHaveBeenCalledWith(descriptor.wsUrl)
    await vi.waitFor(() => expect($activeGatewayProfile.get()).toBe('research'))
    expect($connection.get()).toBe(descriptor)
  })
})
