import { afterEach, describe, expect, it } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import type { SessionInfo } from '@/types/hermes'

import { setSessions } from './session'
import { $sessionDotStateById, hasLiveTurn, showsRunningArc } from './session-dot-state'
import { clearAllSessionStates, publishSessionState, sessionStatusKey } from './session-states'

afterEach(() => {
  clearAllSessionStates()
  setSessions([])
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
})
