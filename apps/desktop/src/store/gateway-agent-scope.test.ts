import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Registry-agent scoping regression (multi-source roster): the active key can
// name a `conn:<id>::<profile>` scope whose secondaries entry gets evicted by
// a teardown path (closeSecondaryGateways during a soft gateway switch,
// pruneSecondaryGateways). activeGateway() used to silently fall back to the
// PRIMARY gateway for a missing NAMED scope — every send/session op then hit
// the wrong machine with no error. These tests pin the invariant: a missing
// named scope never resolves to the primary; every eviction site explicitly
// re-points the active key at the primary so `activeKey` always resolves.

const gatewayMocks = vi.hoisted(() => ({
  connect: vi.fn(async (_wsUrl: string): Promise<void> => undefined),
  setConnection: vi.fn(),
  setGatewayState: vi.fn()
}))

vi.mock('@/hermes', () => ({
  setApiRequestConnection: vi.fn(),
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
  }
}))
vi.mock('@/store/session', () => ({
  setConnection: gatewayMocks.setConnection,
  setGatewayState: gatewayMocks.setGatewayState
}))
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: vi.fn() }))

const {
  $gateway,
  activeGateway,
  closeSecondaryGateways,
  configureGatewayRegistry,
  disposeSecondariesForConnection,
  ensureGatewayForAgent,
  ensureGatewayForProfile,
  isActivePrimary,
  prepareGatewayForAgent,
  pruneSecondaryGateways,
  setPrimaryGateway
} = await import('./gateway')

interface DesktopStub {
  getConnection: ReturnType<typeof vi.fn>
  getConnectionFor: ReturnType<typeof vi.fn>
}

function installDesktop(stub: DesktopStub): void {
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = stub
}

function makePrimary(): { connectionState: string } {
  return { connectionState: 'open' }
}

const agentConn = {
  authMode: 'token',
  baseUrl: 'https://homelab.invalid',
  mode: 'remote',
  profile: 'research',
  token: 'fake-test-token',
  wsUrl: 'wss://homelab.invalid/api/ws?token=fake-test-token'
}

function installAgentDesktop(): DesktopStub {
  const stub: DesktopStub = {
    getConnection: vi.fn(async () => agentConn),
    getConnectionFor: vi.fn(async () => agentConn)
  }

  installDesktop(stub)

  return stub
}

beforeEach(() => {
  configureGatewayRegistry({ onEvent: vi.fn() })
})

afterEach(() => {
  closeSecondaryGateways()
  vi.clearAllMocks()
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('registry-agent scope eviction (activeGateway must never silently hit the primary)', () => {
  it('reuses one successful registry descriptor for the exact socket dial', async () => {
    const descriptor = { ...agentConn, token: 'only-ticket', wsUrl: 'wss://homelab.invalid/api/ws?token=only-ticket' }

    const getConnectionFor = vi.fn(async () => ({
      ...agentConn,
      token: 'second-ticket',
      wsUrl: 'wss://wrong.invalid/api/ws?token=second-ticket'
    }))

    setPrimaryGateway(makePrimary() as never, 'default')
    installDesktop({ getConnection: vi.fn(async () => descriptor), getConnectionFor })

    const activate = await prepareGatewayForAgent('homelab', 'research', descriptor as never)

    expect(getConnectionFor).not.toHaveBeenCalled()
    expect(gatewayMocks.connect).toHaveBeenCalledOnce()
    expect(gatewayMocks.connect).toHaveBeenCalledWith(descriptor.wsUrl)
    expect(activate()).toBe(true)
  })

  it('reconnects an active closed agent silently until its prepared activation is invoked', async () => {
    const onActiveConnectionChanged = vi.fn()

    configureGatewayRegistry({ onActiveConnectionChanged, onEvent: vi.fn() })
    setPrimaryGateway(makePrimary() as never, 'default')
    installAgentDesktop()

    await ensureGatewayForAgent('homelab', 'research')
    expect(onActiveConnectionChanged).toHaveBeenCalledOnce()

    onActiveConnectionChanged.mockClear()
    gatewayMocks.connect.mockClear()
    ;(activeGateway() as unknown as { close: () => void }).close()
    gatewayMocks.setGatewayState.mockClear()

    const activate = await prepareGatewayForAgent('homelab', 'research', agentConn as never)

    expect(gatewayMocks.connect).toHaveBeenCalledOnce()
    expect(onActiveConnectionChanged).not.toHaveBeenCalled()
    expect(gatewayMocks.setGatewayState).not.toHaveBeenCalled()

    expect(activate()).toBe(true)
    expect(onActiveConnectionChanged).toHaveBeenCalledOnce()
    expect(onActiveConnectionChanged).toHaveBeenCalledWith(agentConn)
    expect(gatewayMocks.setGatewayState).toHaveBeenCalledOnce()
    expect(gatewayMocks.setGatewayState).toHaveBeenCalledWith('open')
  })

  it('rejects a prepared activation superseded by a real gateway activation epoch', async () => {
    const onActiveConnectionChanged = vi.fn()

    const sourceB = {
      ...agentConn,
      baseUrl: 'https://source-b.invalid',
      token: 'source-b-ticket',
      wsUrl: 'wss://source-b.invalid/api/ws?token=source-b-ticket'
    }

    configureGatewayRegistry({ onActiveConnectionChanged, onEvent: vi.fn() })
    setPrimaryGateway(makePrimary() as never, 'default')
    installAgentDesktop()

    const activateA = await prepareGatewayForAgent('source-a', 'research', agentConn as never)
    const activateB = await prepareGatewayForAgent('source-b', 'research', sourceB as never)

    expect(onActiveConnectionChanged).not.toHaveBeenCalled()
    expect(activateB()).toBe(true)
    expect(onActiveConnectionChanged).toHaveBeenCalledOnce()
    expect(onActiveConnectionChanged).toHaveBeenLastCalledWith(sourceB)

    onActiveConnectionChanged.mockClear()
    expect(activateA()).toBe(false)
    expect(onActiveConnectionChanged).not.toHaveBeenCalled()
  })

  it('rejects a prepared activation after its exact registry entry is disposed', async () => {
    const onActiveConnectionChanged = vi.fn()

    configureGatewayRegistry({ onActiveConnectionChanged, onEvent: vi.fn() })
    setPrimaryGateway(makePrimary() as never, 'default')
    installAgentDesktop()

    const activate = await prepareGatewayForAgent('removed-source', 'research', agentConn as never)
    disposeSecondariesForConnection('removed-source')

    expect(activate()).toBe(false)
    expect(onActiveConnectionChanged).not.toHaveBeenCalled()
  })

  it('activates the agent socket, not the primary, for a registry scope', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installAgentDesktop()

    await ensureGatewayForAgent('homelab', 'research')

    expect(isActivePrimary()).toBe(false)
    expect(activeGateway()).not.toBe(primary)
    expect($gateway.get()).not.toBe(primary)
    expect(gatewayMocks.connect).toHaveBeenCalledWith(agentConn.wsUrl)
  })

  it('keeps the primary active when a fresh registry activation cannot resolve', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    await ensureGatewayForProfile('default')
    const publishedPrimary = $gateway.get()
    installDesktop({
      getConnection: vi.fn(async () => agentConn),
      getConnectionFor: vi.fn(async () => {
        throw new Error('source unreachable')
      })
    })

    await expect(ensureGatewayForAgent('offline', 'research')).resolves.toBe(false)

    expect(isActivePrimary()).toBe(true)
    expect(activeGateway()).toBe(primary)
    expect($gateway.get()).toBe(publishedPrimary)
  })

  it('closeSecondaryGateways re-points the active key at the primary instead of dangling', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installAgentDesktop()

    await ensureGatewayForAgent('homelab', 'research')
    expect(isActivePrimary()).toBe(false)

    // The soft gateway switch path (use-gateway-boot) tears every secondary
    // down without knowing which one was active.
    closeSecondaryGateways()

    // Regression: activeKey used to keep naming the evicted scope while
    // activeGateway() fell back to the primary — wrong machine, no error.
    // Now the teardown restores the primary EXPLICITLY (atoms follow).
    expect(isActivePrimary()).toBe(true)
    expect(activeGateway()).toBe(primary)
    expect($gateway.get()).toBe(primary)
  })

  it('pruneSecondaryGateways never evicts the active scope and keeps the invariant', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installAgentDesktop()

    await ensureGatewayForAgent('homelab', 'research')
    const agentGateway = activeGateway()

    // Empty keep-set: everything non-active is evictable — the active agent
    // scope must survive, and the active pointer must still resolve to it.
    pruneSecondaryGateways(new Set())

    expect(isActivePrimary()).toBe(false)
    expect(activeGateway()).toBe(agentGateway)
    expect($gateway.get()).toBe(agentGateway)
  })
})
