import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import type * as HermesModule from '@/hermes'
import { chatMessageText } from '@/lib/chat-messages'
import { createClientSessionState } from '@/lib/chat-runtime'
import type * as ProfileModule from '@/store/profile'
import { setSessions } from '@/store/session'
import { $sessionTiles, sessionTileDelegate } from '@/store/session-states'
import type { SessionInfo } from '@/types/hermes'

import { useSessionTileDelegate } from './use-session-tile-delegate'

vi.mock('@/hermes', async importActual => ({
  ...(await importActual<typeof HermesModule>()),
  getLatestSessionMessages: vi.fn(async () => ({ messages: [], session_id: '' }))
}))

vi.mock('@/store/profile', async importActual => ({
  ...(await importActual<typeof ProfileModule>()),
  ensureGatewayProfile: vi.fn(async () => undefined)
}))

const { getLatestSessionMessages } = await import('@/hermes')

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

    renderTile(requestGateway)
    const runtimeId = await sessionTileDelegate()!.resumeTile('stored-x', 'ai-engineer')

    expect(runtimeId).toBe('runtime-1')
    expect(getLatestSessionMessages).toHaveBeenCalledWith('stored-x', 'ai-engineer')
    expect(requestGateway).toHaveBeenCalledWith('session.resume', {
      session_id: 'stored-x',
      cols: 96,
      profile: 'ai-engineer',
      omit_messages: true
    })
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

  it('does not reuse an empty cached runtime for a stored session with history', async () => {
    setSessions([row({ id: 'stored-z', message_count: 4, profile: 'default' })])

    const emptyCached = { ...createClientSessionState('stored-z'), busy: true }
    const runtimeIdByStoredSessionIdRef = { current: new Map([['stored-z', 'runtime-empty']]) }
    const sessionStateByRuntimeIdRef = { current: new Map([['runtime-empty', emptyCached]]) }

    const updateSessionState = vi.fn((_sessionId, updater, storedSessionId) =>
      updater(createClientSessionState(storedSessionId ?? null))
    )

    const requestGateway = vi.fn(async (method: string) =>
      method === 'session.resume' ? ({ session_id: 'runtime-rebound', messages: [] } as never) : ({} as never)
    )

    vi.mocked(getLatestSessionMessages).mockResolvedValue({
      messages: [
        { content: 'hello', role: 'user', timestamp: 1 },
        { content: 'hi', role: 'assistant', timestamp: 2 }
      ],
      session_id: 'stored-z'
    } as never)

    renderTile(requestGateway, { runtimeIdByStoredSessionIdRef, sessionStateByRuntimeIdRef, updateSessionState })
    const runtimeId = await sessionTileDelegate()!.resumeTile('stored-z', 'default')

    expect(runtimeId).toBe('runtime-rebound')
    expect(getLatestSessionMessages).toHaveBeenCalledWith('stored-z', 'default')
    expect(requestGateway).toHaveBeenCalledWith('session.resume', {
      session_id: 'stored-z',
      cols: 96,
      profile: 'default',
      omit_messages: true
    })
    expect(updateSessionState).toHaveBeenCalled()
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
})
