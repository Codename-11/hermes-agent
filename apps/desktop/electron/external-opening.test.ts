import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, test } from 'vitest'

import { createExternalOpening } from './external-opening'

const roots: string[] = []

afterEach(() => {
  roots.splice(0).forEach(root => fs.rmSync(root, { recursive: true, force: true }))
})

function subject(overrides: Record<string, unknown> = {}) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-external-opening-'))
  roots.push(tempDir)
  const opened: string[] = []
  const revealed: string[] = []

  return {
    opened,
    revealed,
    service: createExternalOpening({
      isWsl: false,
      tempDir,
      rememberLog: () => {},
      resolveRequestedPath: value => new URL(value).pathname,
      shell: {
        openExternal: async value => {
          opened.push(value)
        },
        openPath: async value => {
          opened.push(value)
          return ''
        },
        showItemInFolder: value => {
          revealed.push(value)
        }
      },
      ensureBackend: async () => ({ mode: 'remote', baseUrl: 'https://gateway.example', authMode: 'token', token: 't' }),
      routeRemotePath: value => value,
      fetchBinary: async () => ({ buffer: Buffer.from('file'), headers: {} }),
      fetchBinaryViaOauthSession: async () => ({ buffer: Buffer.from('file'), headers: {} }),
      timeoutMs: 123,
      now: () => 1000,
      randomSuffix: () => 'abcd1234',
      ...overrides
    })
  }
}

test('opens only allowlisted external URL protocols', () => {
  const item = subject()

  assert.equal(item.service.openExternalUrl('javascript:alert(1)'), false)
  assert.equal(item.service.openExternalUrl('https://example.com/path'), true)
  assert.deepEqual(item.opened, ['https://example.com/path'])
})

test('downloads a profile-routed OAuth artifact to a sanitized temporary filename before opening it', async () => {
  const requested: Array<{ url: string; options: unknown }> = []
  const item = subject({
    ensureBackend: async () => ({
      mode: 'remote',
      baseUrl: 'https://gateway.example/',
      authMode: 'oauth',
      remoteProfile: 'work'
    }),
    routeRemotePath: (value: string, profile: string | null, remoteProfile: string | undefined) =>
      `${value}&profile=${remoteProfile || profile}`,
    fetchBinaryViaOauthSession: async (url: string, options: unknown) => {
      requested.push({ url, options })
      return {
        buffer: Buffer.from('artifact'),
        headers: { 'Content-Disposition': 'attachment; filename="bad:name?.txt"' }
      }
    }
  })

  const result = await item.service.openRemoteFile({ path: 'file:///home/a/report.txt', profile: 'desktop' })

  assert.deepEqual(requested, [
    {
      url: 'https://gateway.example/api/files/download?path=%2Fhome%2Fa%2Freport.txt&profile=work',
      options: { timeoutMs: 123 }
    }
  ])
  assert.match(result.path, /1000-abcd1234-bad_name_.txt$/)
  assert.equal(fs.readFileSync(result.path, 'utf8'), 'artifact')
  assert.deepEqual(item.opened, [result.path])
})
