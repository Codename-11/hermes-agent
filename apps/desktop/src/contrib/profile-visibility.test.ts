import { describe, expect, it } from 'vitest'

import type { Contribution } from '@/contrib/types'
import type { ProfileInfo } from '@/types/hermes'

import { filterCoreProfiles, PROFILE_VISIBILITY_AREA } from './profile-visibility'

const profile = (name: string, isDefault = false): ProfileInfo => ({
  has_env: false,
  is_default: isDefault,
  model: null,
  name,
  path: `/profiles/${name}`,
  provider: null,
  skill_count: 0
})

const contribution = (id: string, data: unknown, source = 'plugin:test'): Contribution => ({
  area: PROFILE_VISIBILITY_AREA,
  data,
  id,
  source
})

describe('profile visibility contributions', () => {
  it('hides only identities qualified to the active connection route', () => {
    const profiles = [profile('default', true), profile('worker'), profile('reviewer')]

    const contributions = [
      contribution('test:hidden', {
        hidden: [
          { connectionId: 'homelab', profile: 'worker' },
          { connectionId: 'other-source', profile: 'reviewer' }
        ]
      })
    ]

    expect(filterCoreProfiles(profiles, 'homelab', contributions).map(item => item.name)).toEqual([
      'default',
      'reviewer'
    ])
    expect(filterCoreProfiles(profiles, 'other-source', contributions).map(item => item.name)).toEqual([
      'default',
      'worker'
    ])
  })

  it('ignores malformed, unqualified, and core-owned hide requests', () => {
    const profiles = [profile('default', true), profile('worker')]

    const contributions = [
      contribution('test:bare', { hidden: [{ profile: 'worker' }, 'worker', null] }),
      contribution('core:hidden', { hidden: [{ connectionId: 'homelab', profile: 'worker' }] }, 'core')
    ]

    expect(filterCoreProfiles(profiles, 'homelab', contributions)).toEqual(profiles)
  })

  it('never mutates the authoritative profile inventory', () => {
    const profiles = [profile('default', true), profile('worker')]
    const snapshot = [...profiles]

    const visible = filterCoreProfiles(
      profiles,
      'homelab',
      [contribution('test:hidden', { hidden: [{ connectionId: 'homelab', profile: 'worker' }] })]
    )

    expect(visible).toEqual([profiles[0]])
    expect(profiles).toEqual(snapshot)
  })
})
