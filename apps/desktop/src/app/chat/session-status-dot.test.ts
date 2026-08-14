import { describe, expect, it } from 'vitest'

import { sessionDotClassName } from './session-status-dot'

describe('session running status dot', () => {
  it('does not visually toggle off when a live turn goes quiet', () => {
    expect(sessionDotClassName('stalled')).toBe(sessionDotClassName('working'))
  })
})
