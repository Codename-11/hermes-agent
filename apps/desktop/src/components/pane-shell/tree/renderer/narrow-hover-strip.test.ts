import { describe, expect, it } from 'vitest'

import { narrowHoverStripStyle } from './narrow-hover-strip'

describe('narrowHoverStripStyle', () => {
  it('leaves the native edge and scrollbar hit area unobstructed', () => {
    expect(narrowHoverStripStyle('right')).toEqual({ right: 8, width: 4 })
    expect(narrowHoverStripStyle('left')).toEqual({ left: 8, width: 4 })
  })
})
