import { afterEach, describe, expect, it } from 'vitest'

import {
  $changeEventsAvailable,
  activateChangeEventsProfile,
  resetLiveSync,
  setChangeEventsAvailable
} from './live-sync'

describe('profile-scoped live sync capabilities', () => {
  afterEach(() => resetLiveSync())

  it('does not let a background gateway ready event overwrite the active profile', () => {
    setChangeEventsAvailable(true, 'default', 'default')
    setChangeEventsAvailable(false, 'worker', 'default')

    expect($changeEventsAvailable.get()).toBe(true)

    activateChangeEventsProfile('worker')
    expect($changeEventsAvailable.get()).toBe(false)

    activateChangeEventsProfile('default')
    expect($changeEventsAvailable.get()).toBe(true)
  })
})
