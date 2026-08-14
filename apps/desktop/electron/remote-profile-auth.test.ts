import assert from 'node:assert/strict'

import { test } from 'vitest'

import { fetchRemoteProfilesJson } from './remote-profile-auth'

function makeDeps(nativeToken: string | null) {
  const calls: string[] = []

  const deps = {
    ensureNativeAccessToken: async () => nativeToken,
    fetchBearerJson: async (url: string, bearer: string) => {
      calls.push(`bearer:${url}:${bearer}`)

      return { profiles: [{ name: 'victor' }] }
    },
    fetchCookieJson: async (url: string) => {
      calls.push(`cookie:${url}`)

      return { profiles: [{ name: 'legacy' }] }
    },
    fetchTokenJson: async (url: string, token: string | null) => {
      calls.push(`token:${url}:${token}`)

      return { profiles: [{ name: 'token' }] }
    }
  }

  return { calls, deps }
}

test('remote profile discovery uses the native bearer for a cookieless OAuth session', async () => {
  const { calls, deps } = makeDeps('native-access-token')

  const body = await fetchRemoteProfilesJson(
    { authMode: 'oauth', baseUrl: 'https://gw.example.com', token: null },
    deps
  )

  assert.deepEqual(body, { profiles: [{ name: 'victor' }] })
  assert.deepEqual(calls, ['bearer:https://gw.example.com/api/profiles:native-access-token'])
})

test('remote profile discovery falls back to the legacy OAuth cookie partition', async () => {
  const { calls, deps } = makeDeps(null)

  await fetchRemoteProfilesJson({ authMode: 'oauth', baseUrl: 'https://gw.example.com', token: null }, deps)

  assert.deepEqual(calls, ['cookie:https://gw.example.com/api/profiles'])
})

test('remote profile discovery preserves transient native refresh failures', async () => {
  const { calls, deps } = makeDeps(null)
  const failure = new Error('temporary gateway outage')

  deps.ensureNativeAccessToken = async () => {
    throw failure
  }

  await assert.rejects(
    fetchRemoteProfilesJson({ authMode: 'oauth', baseUrl: 'https://gw.example.com', token: null }, deps),
    error => error === failure
  )

  assert.deepEqual(calls, [])
})

test('remote profile discovery preserves legacy session-token auth', async () => {
  const { calls, deps } = makeDeps('unused-native-token')

  await fetchRemoteProfilesJson(
    { authMode: 'token', baseUrl: 'https://gw.example.com', token: 'session-token' },
    deps
  )

  assert.deepEqual(calls, ['token:https://gw.example.com/api/profiles:session-token'])
})
