import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The global-remote share (backend routing case 3): every profile is served
// by the PRIMARY backend over one host, and getConnection() explicitly tags
// the shared descriptor with `sharedPrimary`. Dialing a second WebSocket at it
// used to fail over SSH (per-backend tunnel/ticket) and poison the active
// gateway with a closed socket — "Hermes gateway is not connected" for every
// profile except the primary. Pooled backends (own-remote override, local
// named profile) also carry `profile` for WS URL minting, so `profile` alone
// cannot identify the shared-primary route. These tests pin the fix: only a
// `sharedPrimary` descriptor activates the primary socket; a pooled descriptor
// that also carries `profile` must still dial its own socket.

const gatewayMocks = vi.hoisted(() => ({
  connect: vi.fn(async (_wsUrl: string): Promise<void> => {
    throw new Error('dialed a socket for a shared-primary profile')
  }),
  markNativeNotifyBaseline: vi.fn(),
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
    close = vi.fn(() => {
      this.emitState('closed')
    })
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
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: gatewayMocks.markNativeNotifyBaseline }))

const {
  $gateway,
  activeGateway,
  closeSecondaryGateways,
  configureGatewayRegistry,
  ensureActiveGatewayOpen,
  ensureGatewayForProfile,
  prepareGatewayForProfile,
  setPrimaryGateway
} = await import('./gateway')

type DesktopStub = { getConnection: ReturnType<typeof vi.fn> }

function installDesktop(stub: DesktopStub): void {
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = stub
}

function makePrimary(): { connectionState: string } {
  // Only connectionState is consulted by setActive/isOpen for these paths.
  return { connectionState: 'open' }
}

beforeEach(() => {
  configureGatewayRegistry({
    onEvent: vi.fn(),
    primaryProfile: 'default'
  } as never)
})

afterEach(() => {
  closeSecondaryGateways()
  vi.clearAllMocks()
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('ensureGatewayForProfile under a shared global remote', () => {
  it('activates the primary socket for an explicitly shared-primary descriptor', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installDesktop({
      // Shared descriptor: primary connection tagged with the profile scope
      // AND the explicit sharedPrimary marker.
      getConnection: vi.fn(async () => ({ port: 4242, profile: 'venture', sharedPrimary: true, token: 't' }))
    })

    await ensureGatewayForProfile('venture')

    expect(gatewayMocks.connect).not.toHaveBeenCalled()
    expect($gateway.get()).toBe(primary)
  })

  it('reuses a pre-resolved shared descriptor without minting another connection ticket', async () => {
    const primary = makePrimary()
    const getConnection = vi.fn(async () => ({ port: 4242, profile: 'venture', sharedPrimary: true, token: 'second-ticket' }))
    const resolved = { port: 4242, profile: 'venture', sharedPrimary: true, token: 'first-ticket' }
    setPrimaryGateway(primary as never, 'default')
    installDesktop({ getConnection })

    await ensureGatewayForProfile('venture', resolved as never)

    expect(getConnection).not.toHaveBeenCalled()
    expect($gateway.get()).toBe(primary)
  })

  it('dials the exact WebSocket URL for a pooled profile descriptor that carries profile', async () => {
    const primary = makePrimary()
    const remoteWsUrl = 'wss://remote.invalid/api/ws?token=fake-test-token'

    setPrimaryGateway(primary as never, 'default')
    installDesktop({
      // Pooled descriptor: carries `profile` for WS URL minting but is NOT
      // shared-primary (no marker) — it must dial its own socket, not reuse
      // the primary. This is the local named / own-remote profile case.
      getConnection: vi.fn(async () => ({
        authMode: 'token',
        baseUrl: 'https://remote.invalid',
        mode: 'remote',
        profile: 'worker',
        token: 'fake-test-token',
        wsUrl: remoteWsUrl
      }))
    })
    gatewayMocks.connect.mockResolvedValueOnce(undefined)

    await ensureGatewayForProfile('worker')

    expect(gatewayMocks.connect).toHaveBeenCalledOnce()
    expect(gatewayMocks.connect).toHaveBeenCalledWith(remoteWsUrl)
    expect($gateway.get()).not.toBe(primary)
  })

  it('reuses a pre-resolved pooled descriptor for classification and the exact socket dial', async () => {
    const primary = makePrimary()

    const getConnection = vi.fn(async () => ({
      authMode: 'token',
      baseUrl: 'https://wrong.invalid',
      mode: 'remote',
      profile: 'worker',
      token: 'second-ticket',
      wsUrl: 'wss://wrong.invalid/api/ws?token=second-ticket'
    }))

    const descriptor = {
      authMode: 'token',
      baseUrl: 'https://worker.invalid',
      mode: 'remote',
      profile: 'worker',
      token: 'only-ticket',
      wsUrl: 'wss://worker.invalid/api/ws?token=only-ticket'
    }

    setPrimaryGateway(primary as never, 'default')
    installDesktop({ getConnection })
    gatewayMocks.connect.mockResolvedValueOnce(undefined)

    await ensureGatewayForProfile('worker', descriptor as never)

    expect(getConnection).not.toHaveBeenCalled()
    expect(gatewayMocks.connect).toHaveBeenCalledOnce()
    expect(gatewayMocks.connect).toHaveBeenCalledWith(descriptor.wsUrl)
  })

  it('reconnects an active closed profile silently until its prepared activation is invoked', async () => {
    const primary = makePrimary()
    const onActiveConnectionChanged = vi.fn()

    const descriptor = {
      authMode: 'token',
      baseUrl: 'https://worker.invalid',
      mode: 'remote',
      profile: 'worker',
      token: 'only-ticket',
      wsUrl: 'wss://worker.invalid/api/ws?token=only-ticket'
    }

    configureGatewayRegistry({ onActiveConnectionChanged, onEvent: vi.fn(), primaryProfile: 'default' } as never)
    setPrimaryGateway(primary as never, 'default')
    installDesktop({ getConnection: vi.fn(async () => descriptor) })
    gatewayMocks.connect.mockResolvedValue(undefined)

    await ensureGatewayForProfile('worker', descriptor as never)
    expect(onActiveConnectionChanged).toHaveBeenCalledOnce()

    onActiveConnectionChanged.mockClear()
    gatewayMocks.connect.mockClear()
    ;(activeGateway() as unknown as { close: () => void }).close()
    gatewayMocks.setGatewayState.mockClear()

    const activate = await prepareGatewayForProfile('worker', descriptor as never)

    expect(gatewayMocks.connect).toHaveBeenCalledOnce()
    expect(onActiveConnectionChanged).not.toHaveBeenCalled()
    expect(gatewayMocks.setGatewayState).not.toHaveBeenCalled()

    expect(activate()).toBe(true)
    expect(onActiveConnectionChanged).toHaveBeenCalledOnce()
    expect(onActiveConnectionChanged).toHaveBeenCalledWith(descriptor)
    expect(gatewayMocks.setGatewayState).toHaveBeenCalledOnce()
    expect(gatewayMocks.setGatewayState).toHaveBeenCalledWith('open')
  })

  it('mutes a joined in-flight reconnect until prepared activation and releases its suppression lease', async () => {
    const primary = makePrimary()
    const onActiveConnectionChanged = vi.fn()
    let resolveReconnect!: () => void

    const descriptor = {
      authMode: 'token',
      baseUrl: 'https://worker.invalid',
      mode: 'remote',
      profile: 'worker',
      token: 'reconnect-ticket',
      wsUrl: 'wss://worker.invalid/api/ws?token=reconnect-ticket'
    }

    const suppliedAfterDialStarted = {
      ...descriptor,
      token: 'too-late-ticket',
      wsUrl: 'wss://worker.invalid/api/ws?token=too-late-ticket'
    }

    configureGatewayRegistry({ onActiveConnectionChanged, onEvent: vi.fn(), primaryProfile: 'default' } as never)
    setPrimaryGateway(primary as never, 'default')
    installDesktop({ getConnection: vi.fn(async () => descriptor) })
    gatewayMocks.connect.mockResolvedValueOnce(undefined)

    await ensureGatewayForProfile('worker', descriptor as never)

    onActiveConnectionChanged.mockClear()
    gatewayMocks.markNativeNotifyBaseline.mockClear()
    gatewayMocks.connect.mockClear()
    gatewayMocks.connect.mockImplementationOnce(
      () =>
        new Promise<void>(resolve => {
          resolveReconnect = resolve
        })
    )
    ;(activeGateway() as unknown as { close: () => void }).close()
    gatewayMocks.setGatewayState.mockClear()

    // A normal reconnect owns the dial first. Preparation must join that exact
    // promise: the already-running descriptor remains authoritative.
    const reconnecting = ensureActiveGatewayOpen()
    await vi.waitFor(() => expect(gatewayMocks.connect).toHaveBeenCalledOnce())
    const preparing = prepareGatewayForProfile('worker', suppliedAfterDialStarted as never)
    await Promise.resolve()
    await Promise.resolve()
    gatewayMocks.setGatewayState.mockClear()

    resolveReconnect()
    await reconnecting
    const activate = await preparing

    expect(gatewayMocks.connect).toHaveBeenCalledOnce()
    expect(onActiveConnectionChanged).not.toHaveBeenCalled()
    expect(gatewayMocks.setGatewayState).not.toHaveBeenCalled()
    expect(gatewayMocks.markNativeNotifyBaseline).toHaveBeenCalledOnce()

    expect(activate()).toBe(true)
    expect(onActiveConnectionChanged).toHaveBeenCalledOnce()
    expect(onActiveConnectionChanged).toHaveBeenCalledWith(descriptor)
    expect(gatewayMocks.setGatewayState).toHaveBeenCalledOnce()
    expect(gatewayMocks.setGatewayState).toHaveBeenCalledWith('open')

    // A later ordinary reconnect publishes normally, proving the preparation
    // lease was released rather than leaking its suppression counter.
    onActiveConnectionChanged.mockClear()
    gatewayMocks.setGatewayState.mockClear()
    gatewayMocks.markNativeNotifyBaseline.mockClear()
    gatewayMocks.connect.mockClear()
    gatewayMocks.connect.mockResolvedValueOnce(undefined)
    ;(activeGateway() as unknown as { close: () => void }).close()
    gatewayMocks.setGatewayState.mockClear()

    await ensureActiveGatewayOpen()

    expect(onActiveConnectionChanged).toHaveBeenCalledOnce()
    expect(onActiveConnectionChanged).toHaveBeenCalledWith(descriptor)
    expect(gatewayMocks.setGatewayState).toHaveBeenCalledTimes(2)
    expect(gatewayMocks.setGatewayState).toHaveBeenNthCalledWith(1, 'connecting')
    expect(gatewayMocks.setGatewayState).toHaveBeenNthCalledWith(2, 'open')
    expect(gatewayMocks.markNativeNotifyBaseline).toHaveBeenCalledOnce()
  })

  it('refreshes the active connection after a pooled profile reconnect succeeds', async () => {
    const connection = {
      authMode: 'token',
      baseUrl: 'https://worker.invalid',
      mode: 'remote',
      profile: 'worker',
      token: 'fake-test-token',
      wsUrl: 'wss://worker.invalid/api/ws?token=fake-test-token'
    }

    const getConnection = vi.fn(async () => connection)

    setPrimaryGateway(makePrimary() as never, 'default')
    installDesktop({ getConnection })

    gatewayMocks.connect.mockRejectedValueOnce(new Error('temporarily offline')).mockResolvedValueOnce(undefined)

    await ensureGatewayForProfile('worker')

    expect(gatewayMocks.setConnection).toHaveBeenCalledOnce()
    expect(gatewayMocks.setConnection).toHaveBeenLastCalledWith(connection)

    await ensureActiveGatewayOpen()

    expect(gatewayMocks.setConnection).toHaveBeenCalledTimes(2)
    expect(gatewayMocks.setConnection).toHaveBeenLastCalledWith(connection)
  })
})
