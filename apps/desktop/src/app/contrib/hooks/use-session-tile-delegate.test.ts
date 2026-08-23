import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import type * as HermesModule from '@/hermes'
import { setSessionOwnerHint, setSessions } from '@/store/session'
import { sessionTileDelegate } from '@/store/session-states'
import type { SessionInfo } from '@/types/hermes'

import { useSessionTileDelegate } from './use-session-tile-delegate'

vi.mock('@/hermes', async importActual => ({
  ...(await importActual<typeof HermesModule>()),
  getLatestSessionMessages: vi.fn(async () => ({ messages: [], session_id: '' }))
}))
vi.mock('@/store/gateway', async importActual => ({
  ...(await importActual<Record<string, unknown>>()),
  requestGatewayForAgent: vi.fn(),
  requestGatewayForProfile: vi.fn()
}))

vi.mock('@/store/profile', async importActual => ({
  ...(await importActual<typeof ProfileModule>()),
  ensureGatewayProfile: vi.fn(async () => undefined)
}))

const { getLatestSessionMessages } = await import('@/hermes')
const { requestGatewayForAgent, requestGatewayForProfile } = await import('@/store/gateway')

const row = (over: Partial<SessionInfo>): SessionInfo =>
  ({
    ended_at: null,
    id: 'live',
    input_tokens: 0,
    is_active: false,
    last_active: 0,
    message_count: 1,
    model: null,
    output_tokens: 0,
    preview: null,
    profile: 'default',
    source: null,
    started_at: 0,
    title: null,
    ...over
  }) as SessionInfo

function renderTile(
  requestGateway: ReturnType<typeof vi.fn>,
  options: {
    runtimeIdByStoredSessionIdRef?: { current: Map<string, string> }
    sessionStateByRuntimeIdRef?: { current: Map<string, ClientSessionState> }
    updateSessionState?: (
      sessionId: string,
      updater: (state: ClientSessionState) => ClientSessionState,
      storedSessionId?: string | null
    ) => ClientSessionState
  } = {}
) {
  renderHook(() =>
    useSessionTileDelegate({
      archiveSession: vi.fn(async () => undefined),
      branchStoredSession: vi.fn(async () => undefined),
      executeSlashCommand: vi.fn(async () => undefined) as never,
      removeSession: vi.fn(async () => undefined),
      requestGateway: requestGateway as never,
      runtimeIdByStoredSessionIdRef: options.runtimeIdByStoredSessionIdRef ?? { current: new Map() },
      sessionStateByRuntimeIdRef: options.sessionStateByRuntimeIdRef ?? { current: new Map() },
      updateSessionState:
        options.updateSessionState ??
        ((_sessionId, updater, storedSessionId) => updater(createClientSessionState(storedSessionId ?? null)))
    })
  )
}

describe('useSessionTileDelegate resumeTile', () => {
  beforeEach(() => {
    setSessions([])
    $sessionTiles.set([])
    vi.mocked(getLatestSessionMessages).mockClear()
  })

  afterEach(() => {
    setSessions([])
    $sessionTiles.set([])
  })

  it('carries the owning profile into a cold tile resume so it cannot fork profiles', async () => {
    // A tile opens a session owned by another profile. Resuming without the
    // profile lets the gateway fall back to the launch-profile DB and clone the
    // conversation into the wrong profile (#67603). The owning profile must ride
    // both the transcript prefetch and the resume RPC.
    setSessions([row({ id: 'stored-x', profile: 'ai-engineer' })])

    const requestGateway = vi.fn(async (method: string) =>
      method === 'session.resume' ? ({ session_id: 'runtime-1' } as never) : ({} as never)
    )

    vi.mocked(requestGatewayForProfile).mockResolvedValueOnce({ session_id: 'runtime-1' } as never)

    renderTile(requestGateway)
    const runtimeId = await sessionTileDelegate()!.resumeTile('stored-x', 'ai-engineer')

    expect(runtimeId).toBe('runtime-1')
    expect(getLatestSessionMessages).toHaveBeenCalledWith('stored-x', 'ai-engineer')
    expect(requestGatewayForProfile).toHaveBeenCalledWith(
      'ai-engineer',
      'session.resume',
      {
        session_id: 'stored-x',
        cols: 96,
        profile: 'ai-engineer',
        omit_messages: true
      },
      undefined,
      undefined
    )
    expect(requestGateway).not.toHaveBeenCalled()
  })

  it('resolves and carries a default-profile session explicitly', async () => {
    setSessions([row({ id: 'stored-y', profile: 'default' })])

    const requestGateway = vi.fn(async (method: string) =>
      method === 'session.resume' ? ({ session_id: 'runtime-2' } as never) : ({} as never)
    )

    renderTile(requestGateway)
    await sessionTileDelegate()!.resumeTile('stored-y', 'default')

    expect(requestGateway).toHaveBeenCalledWith('session.resume', {
      session_id: 'stored-y',
      cols: 96,
      profile: 'default',
      omit_messages: true
    })
  })

  it('routes a Bot tile prefetch and resume through its exact connection owner', async () => {
    const route = {
      connectionId: 'barry',
      mode: 'remote' as const,
      profile: 'oxcoder',
      targetProfile: 'backend-oxcoder'
    }

    setSessionOwnerHint('stored-remote', route)
    vi.mocked(requestGatewayForAgent).mockResolvedValueOnce({ session_id: 'runtime-remote' } as never)
    const ambientRequest = vi.fn(async () => ({}) as never)

    renderTile(ambientRequest)
    const runtimeId = await sessionTileDelegate()!.resumeTile('stored-remote')

    expect(runtimeId).toBe('runtime-remote')
    expect(getLatestSessionMessages).toHaveBeenCalledWith('stored-remote', {
      connectionId: 'barry',
      profile: 'backend-oxcoder'
    })
    expect(requestGatewayForAgent).toHaveBeenCalledWith('barry', 'oxcoder', 'session.resume', {
      session_id: 'stored-remote',
      cols: 96,
      omit_messages: true,
      profile: 'backend-oxcoder'
    })
    expect(ambientRequest).not.toHaveBeenCalled()
  })

  it('reuses a warm binding that still carries a transcript', async () => {
    const state = { ...createClientSessionState('stored-a'), profile: 'default', messages: [{ id: 'm1' }] as never }
    const runtimeIdByStoredSessionIdRef = { current: new Map([['stored-a', 'runtime-a']]) }
    const sessionStateByRuntimeIdRef = { current: new Map([['runtime-a', state]]) }
    const requestGateway = vi.fn(async () => ({}) as never)

    $sessionTiles.set([{ profile: 'default', runtimeId: 'runtime-a', storedSessionId: 'stored-a' }])
    renderTile(requestGateway, { runtimeIdByStoredSessionIdRef, sessionStateByRuntimeIdRef })
    const runtimeId = await sessionTileDelegate()!.resumeTile('stored-a', 'default')

    expect(runtimeId).toBe('runtime-a')
    expect(requestGateway).not.toHaveBeenCalled()
  })

  it('grafts the gateway live projection onto an empty persisted transcript', async () => {
    setSessions([row({ id: 'stored-running', is_active: true, message_count: 3, profile: 'default' })])
    vi.mocked(getLatestSessionMessages).mockResolvedValue({ messages: [], session_id: 'stored-running' } as never)

    const updateSessionState = vi.fn((_sessionId, updater, storedSessionId) =>
      updater(createClientSessionState(storedSessionId ?? null))
    )

    const requestGateway = vi.fn(
      async () =>
        ({
          inflight: { assistant: 'Still working', streaming: true, user: 'Continue OpenShelf' },
          info: { running: true },
          messages: [],
          messages_omitted: true,
          session_id: 'runtime-running'
        }) as never
    )

    renderTile(requestGateway, { updateSessionState })
    await sessionTileDelegate()!.resumeTile('stored-running', 'default')

    const updater = updateSessionState.mock.calls[0]?.[1]
    const hydrated = updater?.(createClientSessionState('stored-running'))

    expect(hydrated?.busy).toBe(true)
    expect(hydrated?.messages.map(chatMessageText)).toEqual(['Continue OpenShelf', 'Still working'])
  })

  it('hard rehydrate clears private mappings without interrupting the backend turn', () => {
    const cached = { ...createClientSessionState('stored-reload'), busy: true }
    const runtimeIdByStoredSessionIdRef = { current: new Map([['stored-reload', 'runtime-reload']]) }
    const sessionStateByRuntimeIdRef = { current: new Map([['runtime-reload', cached]]) }
    const requestGateway = vi.fn()

    $sessionTiles.set([{ profile: 'default', runtimeId: 'runtime-reload', storedSessionId: 'stored-reload' }])
    renderTile(requestGateway, { runtimeIdByStoredSessionIdRef, sessionStateByRuntimeIdRef })
    sessionTileDelegate()!.rehydrateTile('stored-reload', 'default')

    expect(runtimeIdByStoredSessionIdRef.current.has('stored-reload')).toBe(false)
    expect(sessionStateByRuntimeIdRef.current.has('runtime-reload')).toBe(false)
    expect(requestGateway).not.toHaveBeenCalledWith('session.interrupt', expect.anything())
  })

  it('invalidateRuntimeBindings clears the stored→runtime map so tiles re-resume after reconnect', async () => {
    setSessions([row({ id: 'stored-c', profile: 'default' })])

    const liveState = {
      ...createClientSessionState('stored-c'),
      busy: false,
      messages: [{ id: 'm1' }]
    } as ClientSessionState
    const runtimeIdByStoredSessionIdRef = { current: new Map([['stored-c', 'runtime-dead']]) }
    const sessionStateByRuntimeIdRef = { current: new Map([['runtime-dead', liveState]]) }

    const requestGateway = vi.fn(async (method: string) =>
      method === 'session.resume' ? ({ session_id: 'runtime-fresh' } as never) : ({} as never)
    )

    renderTile(requestGateway, { runtimeIdByStoredSessionIdRef, sessionStateByRuntimeIdRef })

    // Gateway reconnect (what resetTileRuntimeBindings calls on wake):
    sessionTileDelegate()!.invalidateRuntimeBindings!()
    expect(runtimeIdByStoredSessionIdRef.current.size).toBe(0)

    // The next resume goes cold instead of reusing the dead binding.
    const runtimeId = await sessionTileDelegate()!.resumeTile('stored-c', 'default')
    expect(runtimeId).toBe('runtime-fresh')
  })
})

describe('useSessionTileDelegate interruptSession', () => {
  beforeEach(() => {
    setSessions([])
  })

  afterEach(async () => {
    setSessions([])
    const { clearSessionRecentlyInterrupted } = await import('../../session/hooks/use-prompt-actions/utils')
    clearSessionRecentlyInterrupted()
  })

  it('marks the session recently interrupted so a quick tile edit/resend still interrupt-firsts (#83855)', async () => {
    const { isSessionRecentlyInterrupted } = await import('../../session/hooks/use-prompt-actions/utils')

    const requestGateway = vi.fn(async () => ({}) as never)

    renderTile(requestGateway)
    await sessionTileDelegate()!.interruptSession('runtime-tile-1', 'default')

    expect(requestGateway).toHaveBeenCalledWith('session.interrupt', { session_id: 'runtime-tile-1' })
    // Same 3s cooldown the primary chat's Stop sets: busy reads false while the
    // gateway winds down, so the rewind path must still interrupt-first.
    expect(isSessionRecentlyInterrupted('runtime-tile-1')).toBe(true)
  })
})
