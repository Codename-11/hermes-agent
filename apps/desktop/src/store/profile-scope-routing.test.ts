import { afterEach, describe, expect, it } from 'vitest'

import {
  $activeGatewayProfile,
  $browsedProfile,
  $profileScope,
  $showAllProfiles,
  ALL_PROFILES
} from './profile'

afterEach(() => {
  $activeGatewayProfile.set('default')
  $browsedProfile.set('default')
  $showAllProfiles.set(false)
})

describe('profile browse scope', () => {
  it('does not move the sidebar when a mixed-profile chat activates its request gateway', () => {
    $browsedProfile.set('default')
    $activeGatewayProfile.set('worker')

    expect($profileScope.get()).toBe('default')
  })

  it('follows explicit browse selection and preserves the all-profiles mode', () => {
    $browsedProfile.set('worker')
    expect($profileScope.get()).toBe('worker')

    $showAllProfiles.set(true)
    $activeGatewayProfile.set('default')
    expect($profileScope.get()).toBe(ALL_PROFILES)
  })
})
