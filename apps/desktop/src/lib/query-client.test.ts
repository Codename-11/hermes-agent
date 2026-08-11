import { beforeEach, describe, expect, it, vi } from 'vitest'

import { invalidateProfileScopedQueries, queryClient } from './query-client'

describe('invalidateProfileScopedQueries', () => {
  beforeEach(() => {
    queryClient.clear()
  })

  it('resets profile-scoped caches and leaves account/global caches intact', () => {
    const profileScoped = [
      ['hermes-config-record'],
      ['hermes-config-schema'],
      ['skills-list'],
      ['toolsets-list'],
      ['model-options', 'global'],
      ['command-palette', 'sessions'],
      ['session-picker', 'sessions']
    ]

    const global = [
      ['billing', 'state'],
      ['billing', 'subscription'],
      ['marketplace-themes', 'all'],
      ['marketplace-themes-settings', 'x'],
      ['onboarding-model-options', 'y'],
      ['contrib-logs-tail']
    ]

    for (const key of [...profileScoped, ...global]) {
      queryClient.setQueryData(key, { seeded: true })
    }

    invalidateProfileScopedQueries()

    for (const key of profileScoped) {
      expect(queryClient.getQueryData(key), `${JSON.stringify(key)} should be reset`).toBeUndefined()
    }

    for (const key of global) {
      expect(queryClient.getQueryData(key), `${JSON.stringify(key)} should be left intact`).toEqual({ seeded: true })
    }
  })

  it('resets unknown/non-string-rooted keys by default (correctness-safe)', () => {
    queryClient.setQueryData(['some-future-profile-query'], 1)
    queryClient.setQueryData([{ scope: 'weird' }], 1)

    invalidateProfileScopedQueries()

    expect(queryClient.getQueryData(['some-future-profile-query'])).toBeUndefined()
    expect(queryClient.getQueryData([{ scope: 'weird' }])).toBeUndefined()
  })

  it('does not reuse an in-flight request owned by the previous profile', async () => {
    let resolvePrevious!: (value: string) => void
    const previousQuery = vi.fn(
      () =>
        new Promise<string>(resolve => {
          resolvePrevious = resolve
        })
    )
    const key = ['hermes-config-record']
    const stale = queryClient.fetchQuery({ queryKey: key, queryFn: previousQuery })

    await Promise.resolve()
    invalidateProfileScopedQueries()

    const currentQuery = vi.fn(async () => 'profile-b')
    const current = queryClient.fetchQuery({ queryKey: key, queryFn: currentQuery })
    resolvePrevious('profile-a')

    await expect(current).resolves.toBe('profile-b')
    await stale.catch(() => undefined)
    expect(currentQuery).toHaveBeenCalledOnce()
    expect(queryClient.getQueryData(key)).toBe('profile-b')
  })
})
