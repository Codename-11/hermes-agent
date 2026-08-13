import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/types/hermes'

import { $selectedStoredSessionId, $sessions } from './session'
import {
  $switcherIndex,
  $switcherOpen,
  $switcherSessions,
  closeSwitcher,
  commitOnCtrlUp,
  onSwitcherTabDown,
  onSwitcherTabUp,
  openOrAdvanceSwitcher,
  slotSession,
  SWITCHER_REVEAL_MS
} from './session-switcher'

const session = (id: string, profile?: string): SessionInfo => ({ id, profile }) as SessionInfo

const seed = (ids: string[], selected: null | string) => {
  $sessions.set(ids.map(id => session(id)))
  $selectedStoredSessionId.set(selected)
}

const tabTap = (direction: 1 | -1 = 1) => {
  onSwitcherTabDown()
  const target = openOrAdvanceSwitcher(direction)
  onSwitcherTabUp()

  return target
}

beforeEach(() => {
  vi.useRealTimers()
  closeSwitcher()
  $switcherSessions.set([])
  $switcherIndex.set(0)
})

afterEach(() => {
  seed([], null)
})

describe('openOrAdvanceSwitcher', () => {
  it('does nothing with fewer than two sessions', () => {
    seed(['a'], 'a')
    onSwitcherTabDown()

    expect(openOrAdvanceSwitcher(1)).toBeNull()
  })

  it('jumps immediately on a quick Tab tap without opening the HUD', () => {
    seed(['a', 'b', 'c'], 'a')

    expect(tabTap()).toEqual({ id: 'b', profile: undefined })
    expect($switcherOpen.get()).toBe(false)
    expect(commitOnCtrlUp()).toBeNull()
  })

  it('does not open the HUD when Ctrl stays down but Tab was released quickly', () => {
    vi.useFakeTimers()
    seed(['a', 'b', 'c'], 'a')

    tabTap()
    vi.advanceTimersByTime(SWITCHER_REVEAL_MS)

    expect($switcherOpen.get()).toBe(false)
  })

  it('opens the HUD when Tab stays held past the reveal delay', () => {
    vi.useFakeTimers()
    seed(['a', 'b', 'c'], 'a')

    onSwitcherTabDown()
    openOrAdvanceSwitcher(1)
    vi.advanceTimersByTime(SWITCHER_REVEAL_MS)

    expect($switcherOpen.get()).toBe(true)
    onSwitcherTabUp()
  })

  it('opens on a second Tab while Ctrl is still down', () => {
    seed(['a', 'b', 'c'], 'a')

    expect(tabTap()).toEqual({ id: 'b', profile: undefined })
    onSwitcherTabDown()
    openOrAdvanceSwitcher(1)
    onSwitcherTabUp()

    expect($switcherOpen.get()).toBe(true)
    expect($switcherIndex.get()).toBe(2)
  })

  it('commits the HUD highlight on Ctrl up', () => {
    seed(['a', 'b', 'c'], 'a')

    expect(tabTap()).toEqual({ id: 'b', profile: undefined })
    onSwitcherTabDown()
    openOrAdvanceSwitcher(1)
    onSwitcherTabUp()

    expect(commitOnCtrlUp()).toEqual({ id: 'c', profile: undefined })
  })

  it('preserves profile identity for duplicate session ids', () => {
    $sessions.set([session('shared', 'victor'), session('shared', 'sentinel')])
    $selectedStoredSessionId.set('shared')

    expect(tabTap()).toEqual({ id: 'shared', profile: 'sentinel' })
  })
})

describe('slotSession', () => {
  it('reads the armed snapshot while browsing is pending', () => {
    seed(['a', 'b', 'c'], 'a')
    tabTap()
    $sessions.set([session('x')])

    expect(slotSession(2)).toEqual({ id: 'b', profile: undefined })
  })

  it('preserves the profile from the armed snapshot', () => {
    $sessions.set([session('shared', 'victor'), session('shared', 'sentinel')])
    $selectedStoredSessionId.set('shared')
    tabTap()

    expect(slotSession(2)).toEqual({ id: 'shared', profile: 'sentinel' })
  })
})
