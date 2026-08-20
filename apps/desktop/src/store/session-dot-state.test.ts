import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import type { SessionInfo } from '@/types/hermes'

import { $activeGatewayProfile } from './profile-scope'
import { $sessions, $unreadFinishedSessionIds, setSessions } from './session'
import {
  $delegatingSessionIds,
  $sessionDotStateById,
  hasLiveTurn,
  showsRunningArc,
  unreadSessionCount
} from './session-dot-state'
import { clearAllSessionStates, publishSessionState, sessionStatusKey } from './session-states'
import { $unreadWriteGuard, unreadWriteGuardKey } from './session-unread-remote'
import { $subagentsBySession, type SubagentProgress } from './subagents'

afterEach(() => {
  clearAllSessionStates()
  setSessions([])
  $activeGatewayProfile.set('default')
})

describe('showsRunningArc', () => {
  it('keeps the arc when an authoritative turn goes quiet', () => {
    expect(showsRunningArc('working')).toBe(true)
    expect(showsRunningArc('stalled')).toBe(true)
  })

  it('yields to the needs-input treatment rather than running both', () => {
    expect(showsRunningArc('needs-input')).toBe(false)
  })

  it('leaves a session that is not running unmarked', () => {
    expect(showsRunningArc('background')).toBe(false)
    expect(showsRunningArc('idle')).toBe(false)
    expect(showsRunningArc('unread')).toBe(false)
  })
})

describe('hasLiveTurn', () => {
  it('counts a turn waiting on an answer as still live', () => {
    expect(hasLiveTurn('needs-input')).toBe(true)
  })

  it('covers everything the arc covers', () => {
    for (const state of ['background', 'idle', 'needs-input', 'stalled', 'unread', 'working'] as const) {
      expect(hasLiveTurn(state) || !showsRunningArc(state)).toBe(true)
    }
  })

  it('excludes work that outlived the turn', () => {
    expect(hasLiveTurn('background')).toBe(false)
    expect(hasLiveTurn('unread')).toBe(false)
  })
})

describe('profile-scoped status identity', () => {
  it('does not mark a cloned session id working in another profile', () => {
    setSessions([
      { id: 'shared-id', profile: 'default' } as SessionInfo,
      { id: 'shared-id', profile: 'worker' } as SessionInfo
    ])
    publishSessionState('runtime-default', {
      ...createClientSessionState('shared-id'),
      busy: true,
      profile: 'default'
    })

    expect($sessionDotStateById.get()[sessionStatusKey('default', 'shared-id')]).toBe('working')
    expect($sessionDotStateById.get()[sessionStatusKey('worker', 'shared-id')]).toBeUndefined()
  })

  it('uses the unique stored-session owner when a live state has no profile yet', () => {
    setSessions([{ id: 'stored-worker', profile: 'worker' } as SessionInfo])
    publishSessionState('runtime-worker', {
      ...createClientSessionState('stored-worker'),
      busy: true
    })

    expect($sessionDotStateById.get()[sessionStatusKey('worker', 'stored-worker')]).toBe('working')
    expect($sessionDotStateById.get()[sessionStatusKey('default', 'stored-worker')]).toBeUndefined()
  })

  it('does not guess an owner when the same stored id exists in multiple profiles', () => {
    setSessions([
      { id: 'shared-id', profile: 'default' } as SessionInfo,
      { id: 'shared-id', profile: 'worker' } as SessionInfo
    ])
    publishSessionState('runtime-unknown', {
      ...createClientSessionState('shared-id'),
      busy: true
    })

    expect($sessionDotStateById.get()[sessionStatusKey('worker', 'shared-id')]).toBeUndefined()
    expect($sessionDotStateById.get()[sessionStatusKey('default', 'shared-id')]).toBeUndefined()
  })

  it('keeps the bare-id compatibility view pinned to the active profile', () => {
    setSessions([
      storedRow('shared-id', { profile: 'default', unread: true }),
      storedRow('shared-id', { profile: 'worker', unread: false })
    ])
    publishSessionState('runtime-worker', {
      ...createClientSessionState('shared-id'),
      busy: true,
      profile: 'worker'
    })

    expect($sessionDotStateById.get()['shared-id']).toBe('unread')
    expect($sessionDotStateById.get()[sessionStatusKey('worker', 'shared-id')]).toBe('working')

    $activeGatewayProfile.set('worker')
    expect($sessionDotStateById.get()['shared-id']).toBe('working')
  })
})

describe('$delegatingSessionIds', () => {
  const subagent = (status: SubagentProgress['status']): SubagentProgress => ({
    id: 'sub-1',
    parentId: null,
    goal: 'do a thing',
    status,
    taskCount: 1,
    taskIndex: 0,
    startedAt: 0,
    updatedAt: 0,
    filesRead: [],
    filesWritten: [],
    stream: []
  })

  afterEach(() => {
    clearAllSessionStates()
    $subagentsBySession.set({})
    $sessions.set([])
  })

  it('claims the stored id while a subagent is running after the parent turn ended', () => {
    publishSessionState('runtime-1', { ...createClientSessionState('stored-1'), busy: false })
    $subagentsBySession.set({ 'runtime-1': [subagent('running')] })

    expect($delegatingSessionIds.get()).toContain('stored-1')
  })

  it('drops the session once every subagent reaches a terminal status', () => {
    publishSessionState('runtime-1', { ...createClientSessionState('stored-1'), busy: false })
    $subagentsBySession.set({ 'runtime-1': [subagent('running')] })
    $subagentsBySession.set({ 'runtime-1': [subagent('completed')] })

    expect($delegatingSessionIds.get()).not.toContain('stored-1')
  })

  it('falls back to the runtime id for a not-yet-persisted conversation', () => {
    $subagentsBySession.set({ 'runtime-fresh': [subagent('queued')] })

    expect($delegatingSessionIds.get()).toContain('runtime-fresh')
  })
})

const storedRow = (id: string, extra: Partial<SessionInfo> = {}): SessionInfo =>
  ({ id, message_count: 1, source: 'cli', started_at: 0, title: id, ...extra }) as SessionInfo

describe('persisted unread (backend watermark)', () => {
  beforeEach(() => {
    clearAllSessionStates()
    $sessions.set([])
    $unreadFinishedSessionIds.set([])
    $unreadWriteGuard.set(new Map())
  })

  afterEach(() => {
    clearAllSessionStates()
    $sessions.set([])
    $unreadFinishedSessionIds.set([])
    $unreadWriteGuard.set(new Map())
  })

  it('claims unread for a session whose row carries unread: true', () => {
    setSessions([storedRow('s1', { unread: true })])

    expect($sessionDotStateById.get()['s1']).toBe('unread')
  })

  it('does not leak persisted unread to a duplicate id in another profile', () => {
    setSessions([
      storedRow('s1', { profile: 'default', unread: true }),
      storedRow('s1', { profile: 'worker', unread: false })
    ])

    expect($sessionDotStateById.get()[sessionStatusKey('default', 's1')]).toBe('unread')
    expect($sessionDotStateById.get()[sessionStatusKey('worker', 's1')]).toBeUndefined()
  })

  it('keeps draft weaker than persisted unread', () => {
    // A blank tile (no busy, no messages, message_count 0) is a draft; the
    // persisted unread claim must speak over it.
    setSessions([storedRow('s1', { message_count: 0, unread: true })])
    publishSessionState('rt1', createClientSessionState('s1'))

    expect($sessionDotStateById.get()['s1']).toBe('unread')
  })

  it('lets working outrank persisted unread', () => {
    setSessions([storedRow('s1', { unread: true })])
    publishSessionState('rt1', { ...createClientSessionState('s1'), busy: true })

    expect($sessionDotStateById.get()['s1']).toBe('working')
  })

  it('fences a stale page with the write guard', () => {
    const key = unreadWriteGuardKey('s1', 'default')
    const guard = new Map($unreadWriteGuard.get())
    guard.set(key, { at: Date.now(), profile: 'default', storedId: 's1', value: true })
    $unreadWriteGuard.set(guard)

    // A page issued before our PATCH still says read — keep OUR value.
    setSessions([storedRow('s1', { unread: false })])
    expect($sessionDotStateById.get()['s1']).toBe('unread')

    // The guard expires: the page wins and the dot drops.
    const expired = new Map($unreadWriteGuard.get())
    expired.set(key, { at: Date.now() - 60_000, profile: 'default', storedId: 's1', value: true })
    $unreadWriteGuard.set(expired)
    expect($sessionDotStateById.get()['s1']).not.toBe('unread')
  })

  it('leaves a row alone when the backend omits the flag (older runtime)', () => {
    setSessions([storedRow('s1')])

    expect($sessionDotStateById.get()['s1'] ?? 'idle').not.toBe('unread')
  })
})

describe('unreadSessionCount', () => {
  it('counts listed unread rows and skips archived', () => {
    expect(
      unreadSessionCount({ a: 'unread', b: 'working', c: 'unread' }, [
        { id: 'a' },
        { id: 'b' },
        { archived: true, id: 'c' },
        { id: 'missing' }
      ])
    ).toBe(1)
  })

  it('does not count alias keys that are not listed rows', () => {
    expect(unreadSessionCount({ tip: 'unread', root: 'unread' }, [{ id: 'tip' }])).toBe(1)
  })
})
