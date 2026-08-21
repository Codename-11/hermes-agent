import { describe, expect, it } from 'vitest'

import { FILE_BROWSER_MAX_WIDTH } from './layout'

describe('file browser width contract', () => {
  it('defaults to 20rem while allowing fork skins to override through CSS', () => {
    expect(FILE_BROWSER_MAX_WIDTH).toBe('var(--hermes-file-browser-max-width, 20rem)')
  })
})
