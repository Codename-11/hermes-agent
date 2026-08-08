import { describe, expect, it } from 'vitest'

import { friendlyError, hasUpdate, shortSha } from './model'

describe('update summaries', () => {
  it('treats either the explicit flag or a positive behind count as an update', () => {
    expect(hasUpdate({ supported: true, updateAvailable: true })).toBe(true)
    expect(hasUpdate({ supported: true, behind: 2 })).toBe(true)
    expect(hasUpdate({ supported: false, behind: 2 })).toBe(false)
    expect(hasUpdate({ supported: true, behind: 0 })).toBe(false)
  })

  it('shortens commit identifiers without inventing one', () => {
    expect(shortSha('1234567890abcdef')).toBe('12345678')
    expect(shortSha('abc')).toBe('abc')
    expect(shortSha()).toBe('—')
  })

  it('turns unknown failures into useful general-purpose copy', () => {
    expect(friendlyError(new Error('bridge offline'))).toBe('bridge offline')
    expect(friendlyError('timeout')).toBe('timeout')
    expect(friendlyError(null)).toBe('Update information is unavailable right now.')
  })
})
