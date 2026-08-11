import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import { $selectedStoredSessionId, $unreadFinishedSessionIds, setSessions } from '@/store/session'
import {
  $attentionSessionIds,
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
    $unreadFinishedSessionIds.set([])
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    clearAllSessionStates()
    setSessions([])
    $unreadFinishedSessionIds.set([])
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
})
