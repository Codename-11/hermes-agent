import { describe, expect, it } from 'vitest'

import { sessionPaletteItemId } from './session-item-id'

describe('sessionPaletteItemId', () => {
  it('separates cloned-profile copies of the same session id', () => {
    expect(sessionPaletteItemId({ id: 'shared', profile: 'victor' })).not.toBe(
      sessionPaletteItemId({ id: 'shared', profile: 'sentinel' })
    )
  })

  it('normalizes an omitted profile to the default identity', () => {
    expect(sessionPaletteItemId({ id: 'session-1' })).toBe('session-default-session-1')
  })
})
