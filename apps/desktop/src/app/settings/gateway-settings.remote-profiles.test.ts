import { describe, expect, it } from 'vitest'

import { normalizeRemoteProfileHandle, suggestRemoteProfileHandle } from './gateway-settings'

describe('remote profile local handles', () => {
  it('normalizes free-form handle input to profile-safe names', () => {
    expect(normalizeRemoteProfileHandle(' TGI Atlas ')).toBe('tgi-atlas')
    expect(normalizeRemoteProfileHandle('server/default')).toBe('server-default')
    expect(normalizeRemoteProfileHandle('Atlas__Remote')).toBe('atlas__remote')
  })

  it('suggests a host-qualified handle for remote default profiles', () => {
    expect(suggestRemoteProfileHandle('default', 'http://tgi-http.tgi.local:9119', new Set(['default']))).toBe(
      'tgi-http-default'
    )
  })

  it('deduplicates suggested handles against local profiles and prior suggestions', () => {
    const used = new Set(['atlas', 'atlas-2'])
    expect(suggestRemoteProfileHandle('Atlas', 'http://remote.example', used)).toBe('atlas-3')
  })
})
