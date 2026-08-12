import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import type * as HermesModule from '@/hermes'
import { createClientSessionState } from '@/lib/chat-runtime'
import { setSessions } from '@/store/session'
import { sessionTileDelegate } from '@/store/session-states'
import type { SessionInfo } from '@/types/hermes'

import { useSessionTileDelegate } from './use-session-tile-delegate'

vi.mock('@/hermes', async importActual => ({
  ...(await importActual<typeof HermesModule>()),
  getLatestSessionMessages: vi.fn(async () => ({ messages: [], session_id: '' }))
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
    vi.mocked(getLatestSessionMessages).mockClear()
  })

  afterEach(() => {
    setSessions([])
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
    const runtimeId = await sessionTileDelegate()!.resumeTile('stored-x')

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
    await sessionTileDelegate()!.resumeTile('stored-y')

    expect(requestGateway).toHaveBeenCalledWith('session.resume', {
      session_id: 'stored-y',
      cols: 96,
      profile: 'default',
      omit_messages: true
    })
  })

  it('does not reuse an empty cached runtime for a stored session with history', async () => {
    setSessions([row({ id: 'stored-z', message_count: 4, profile: 'default' })])

    const emptyCached = createClientSessionState('stored-z')
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
    const runtimeId = await sessionTileDelegate()!.resumeTile('stored-z')

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
})
