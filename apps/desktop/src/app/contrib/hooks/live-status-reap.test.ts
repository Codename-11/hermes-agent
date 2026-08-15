import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { createClientSessionState } from '@/lib/chat-runtime'
import { captureLiveSessionStatusBaseline, setLiveSessionStateReconciler } from '@/store/live-session-status'
import { $activeSessionId, $selectedStoredSessionId, $unreadFinishedSessionIds, setSessions } from '@/store/session'
import { createClientSessionState } from '@/lib/chat-runtime'
import { $activeSessionId, $selectedStoredSessionId, $unreadFinishedSessionIds } from '@/store/session'
import {
  $attentionSessionIds,
  $sessionStates,
  $workingSessionIds,
  clearAllSessionStates,
  publishSessionState
} from '@/store/session-states'
import type { SessionInfo } from '@/types/hermes'

import { rehydrateLiveSessionStatuses } from './use-background-sync'

/**
 * `session.active_list` is the authoritative snapshot of what is RUNNING in the
 * polled gateway process. A session that finished while Desktop was looking
 * elsewhere — or whose runtime id was recycled by a backend respawn — simply
 * stops appearing in the response. Absence is therefore a completion signal,
 * not "no news": if nothing reaps it, the row spins forever and the
 * busy→idle edge that paints the green "your turn" dot never fires.
 */
describe('rehydrateLiveSessionStatuses — reaping vanished runtimes', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    $selectedStoredSessionId.set(null)
    $activeSessionId.set(null)
    $unreadFinishedSessionIds.set([])
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    clearAllSessionStates()
    setSessions([])
    $unreadFinishedSessionIds.set([])
    $activeSessionId.set(null)
  })

  it('clears a working session that disappears from the live snapshot', () => {
    rehydrateLiveSessionStatuses({
      sessions: [{ id: 'runtime-a', session_key: 'stored-a', status: 'working' }]
    })

    expect($workingSessionIds.get()).toEqual(['stored-a'])

    // The turn finished and the gateway reaped the session between polls.
    rehydrateLiveSessionStatuses({ sessions: [] })

    expect($workingSessionIds.get()).toEqual([])
  })

  it('fires the unread "your turn" marker for a vanished background session', () => {
    rehydrateLiveSessionStatuses({
      sessions: [{ id: 'runtime-b', session_key: 'stored-b', status: 'working' }]
    })

    rehydrateLiveSessionStatuses({ sessions: [] })

    expect($unreadFinishedSessionIds.get()).toEqual(['stored-b'])
  })

  it('clears a blocked session that disappears from the live snapshot', () => {
    rehydrateLiveSessionStatuses({
      sessions: [{ id: 'runtime-c', session_key: 'stored-c', status: 'waiting' }]
    })

    expect($attentionSessionIds.get()).toEqual(['stored-c'])

    rehydrateLiveSessionStatuses({ sessions: [] })

    expect($attentionSessionIds.get()).toEqual([])
  })

  it('leaves runtimes this poll never seeded alone', () => {
    // A background PROFILE's sessions are served by a different gateway and
    // never appear in this profile's active_list. Reaping them would dark out
    // every other profile's running rows.
    rehydrateLiveSessionStatuses(
      { sessions: [{ id: 'runtime-other', session_key: 'stored-other', status: 'working' }] },
      Date.now(),
      'other'
    )

    rehydrateLiveSessionStatuses({ sessions: [] }, Date.now(), 'default')

    expect($workingSessionIds.get()).toEqual(['stored-other'])
  })

  it('reaps a stream-seeded background runtime on its first reconnect snapshot', () => {
    setSessions([{ id: 'stored-worker', profile: 'worker' } as SessionInfo])
    publishSessionState('runtime-worker', {
      ...createClientSessionState('stored-worker'),
      busy: true,
      storedSessionId: 'stored-worker'
    })

    expect($workingSessionIds.get()).toEqual(['stored-worker'])

    rehydrateLiveSessionStatuses({ sessions: [] }, Date.now(), 'worker', true)

    expect($workingSessionIds.get()).toEqual([])
  })

  it('does not reap another profile during authoritative reconnect', () => {
    setSessions([{ id: 'stored-worker', profile: 'worker' } as SessionInfo])
    publishSessionState('runtime-worker', {
      ...createClientSessionState('stored-worker'),
      busy: true,
      storedSessionId: 'stored-worker'
    })

    rehydrateLiveSessionStatuses({ sessions: [] }, Date.now(), 'default', true)

    expect($workingSessionIds.get()).toEqual(['stored-worker'])
  })

  it('fully settles an idle row through the cache-owned reconciler', () => {
    const cache = new Map<string, ClientSessionState>([
      [
        'runtime-a',
        {
          ...createClientSessionState('stored-a'),
          adoptedRunningTurn: true,
          awaitingResponse: true,
          busy: true,
          interimBoundaryPending: true,
          streamId: 'stream-a',
          turnStartedAt: 123
        }
      ]
    ])
    $activeSessionId.set('runtime-a')
    publishSessionState('runtime-a', cache.get('runtime-a')!)
    const dispose = setLiveSessionStateReconciler((runtimeId, updater, storedSessionId) => {
      const next = updater(cache.get(runtimeId) ?? createClientSessionState(storedSessionId))
      cache.set(runtimeId, next)
      publishSessionState(runtimeId, next)

      return next
    })

    rehydrateLiveSessionStatuses({ sessions: [{ id: 'runtime-a', session_key: 'stored-a', status: 'idle' }] })

    expect(cache.get('runtime-a')).toMatchObject({
      adoptedRunningTurn: false,
      awaitingResponse: false,
      busy: false,
      interimBoundaryPending: false,
      streamId: null,
      turnStartedAt: null
    })
    expect($sessionStates.get()['runtime-a']).toBe(cache.get('runtime-a'))
    dispose()
  })

  it('does not let an older active-list snapshot overwrite a newer stream edge', () => {
    const initial = { ...createClientSessionState('stored-a'), busy: true }
    publishSessionState('runtime-a', initial)
    const baseline = captureLiveSessionStatusBaseline()
    const newer = { ...initial, sawAssistantPayload: true, streamId: 'new-stream' }
    publishSessionState('runtime-a', newer)

    rehydrateLiveSessionStatuses(
      { sessions: [{ id: 'runtime-a', session_key: 'stored-a', status: 'idle' }] },
      Date.now(),
      'default',
      false,
      baseline
    )

    expect($sessionStates.get()['runtime-a']).toBe(newer)
  })

  it('seals open tool parts and clears awaitingResponse when a session vanishes', () => {
    const openTool = {
      type: 'tool-call',
      toolCallId: 'call-1',
      toolName: 'patch',
      args: {},
      argsText: '{}'
    } as never

    publishSessionState('runtime-tools', {
      ...createClientSessionState('stored-tools'),
      busy: true,
      awaitingResponse: true,
      messages: [{ id: 'a1', role: 'assistant', parts: [openTool], pending: false } as never]
    })

    // Keep the runtime referenced so the settled state stays in the store
    // instead of being evicted as no-longer-needed.
    $activeSessionId.set('runtime-tools')

    rehydrateLiveSessionStatuses({
      sessions: [{ id: 'runtime-tools', session_key: 'stored-tools', status: 'working' }]
    })
    rehydrateLiveSessionStatuses({ sessions: [] })

    const state = $sessionStates.get()['runtime-tools']

    expect(state.busy).toBe(false)
    expect(state.awaitingResponse).toBe(false)
    expect((state.messages[0].parts[0] as { result?: unknown }).result).toBeDefined()
  })

  it('clears a session stuck awaiting a response without the busy flag', () => {
    publishSessionState('runtime-await', {
      ...createClientSessionState('stored-await'),
      awaitingResponse: true,
      busy: false
    })

    $activeSessionId.set('runtime-await')

    rehydrateLiveSessionStatuses({
      sessions: [{ id: 'runtime-await', session_key: 'stored-await', status: 'working' }]
    })
    rehydrateLiveSessionStatuses({ sessions: [] })

    expect($sessionStates.get()['runtime-await'].awaitingResponse).toBe(false)
  })
})
