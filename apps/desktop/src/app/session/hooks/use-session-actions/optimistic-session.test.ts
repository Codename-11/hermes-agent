import { describe, expect, it } from 'vitest'

import type { SessionInfo } from '@/types/hermes'

import { mergeOptimisticSession } from './utils'

const session = (id: string, profile: string, title: string): SessionInfo =>
  ({ id, profile, title }) as SessionInfo

describe('mergeOptimisticSession', () => {
  it('replaces only the same profile/id row and preserves a cloned-profile sibling', () => {
    const sentinel = session('shared-id', 'sentinel', 'Sentinel copy')
    const oldVictor = session('shared-id', 'victor', 'Old Victor copy')
    const nextVictor = session('shared-id', 'victor', 'New Victor copy')

    expect(mergeOptimisticSession([oldVictor, sentinel], nextVictor)).toEqual([nextVictor, sentinel])
  })
})
