import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const MAIN_PATH = fileURLToPath(new URL('./main.ts', import.meta.url))

function fetchBinaryHelperSource(): string {
  const source = fs.readFileSync(MAIN_PATH, 'utf8')
  const start = source.indexOf('async function fetchBinaryViaOauthSession')
  const end = source.indexOf('\nconst _nativeTokens', start)

  expect(start).toBeGreaterThanOrEqual(0)
  expect(end).toBeGreaterThan(start)
  return source.slice(start, end)
}

describe('fetchBinaryViaOauthSession native OAuth contract', () => {
  it('attaches a native PKCE bearer token while retaining OAuth session cookies', () => {
    const helper = fetchBinaryHelperSource()

    expect(helper).toContain('ensureNativeAccessToken(parsed.origin)')
    expect(helper).toContain("request.setHeader('Authorization', `Bearer ${nativeAccessToken}`)")
    expect(helper).toContain('session: sess')
    expect(helper).toContain('useSessionCookies: true')
  })
})
