import { promises as fs } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  deleteSession,
  getSession,
  listSessions,
  saveSession,
  setStorePath
} from '../remoteSessions.js'

let storeDir: string
let storePath: string

beforeEach(async () => {
  storeDir = await fs.mkdtemp(join(tmpdir(), 'hermes-remote-sessions-'))
  storePath = join(storeDir, 'remote-sessions.json')
  setStorePath(storePath)
})

afterEach(async () => {
  setStorePath(null)
  await fs.rm(storeDir, { force: true, recursive: true })
})

describe('remoteSessions', () => {
  it('returns null when the file does not exist', async () => {
    await expect(getSession('wss://example:8767')).resolves.toBeNull()
  })

  it('persists a session and reads it back', async () => {
    await saveSession('wss://docker:8767', 'tok-123', '0.8.0-alpha')

    const got = await getSession('wss://docker:8767')
    expect(got).not.toBeNull()
    expect(got!.token).toBe('tok-123')
    expect(got!.serverVersion).toBe('0.8.0-alpha')
    expect(got!.pairedAt).toBeGreaterThan(0)
    expect(got!.certPinSha256).toBeNull()
  })

  it('writes the file with mode 0o600', async () => {
    await saveSession('wss://a:8767', 't', null)
    const st = await fs.stat(storePath)
    // Mask to permission bits; on some CI systems umask leaks extras.
    expect(st.mode & 0o777).toBe(0o600)
  })

  it('overwrites an existing session for the same URL', async () => {
    await saveSession('wss://a:8767', 't1', '0.7.0')
    await saveSession('wss://a:8767', 't2', '0.8.0')

    const got = await getSession('wss://a:8767')
    expect(got!.token).toBe('t2')
    expect(got!.serverVersion).toBe('0.8.0')
  })

  it('preserves previous cert pin when a later save omits it', async () => {
    await saveSession('wss://a:8767', 't1', null, 'sha256-abc')
    await saveSession('wss://a:8767', 't2', '1.0.0')

    const got = await getSession('wss://a:8767')
    expect(got!.certPinSha256).toBe('sha256-abc')
  })

  it('keeps multiple URLs independent', async () => {
    await saveSession('wss://a:8767', 'tok-a', null)
    await saveSession('wss://b:8767', 'tok-b', null)

    const all = await listSessions()
    expect(Object.keys(all).sort()).toEqual(['wss://a:8767', 'wss://b:8767'])
    expect(all['wss://a:8767']!.token).toBe('tok-a')
    expect(all['wss://b:8767']!.token).toBe('tok-b')
  })

  it('deleteSession removes only the target URL', async () => {
    await saveSession('wss://a:8767', 'tok-a', null)
    await saveSession('wss://b:8767', 'tok-b', null)

    await deleteSession('wss://a:8767')
    await expect(getSession('wss://a:8767')).resolves.toBeNull()
    const b = await getSession('wss://b:8767')
    expect(b!.token).toBe('tok-b')
  })

  it('fails closed on malformed JSON', async () => {
    await fs.writeFile(storePath, '{ not json', 'utf8')
    await expect(getSession('wss://x:8767')).resolves.toBeNull()
    await expect(listSessions()).resolves.toEqual({})
  })

  it('fails closed on unexpected shape (array at top-level)', async () => {
    await fs.writeFile(storePath, '[]', 'utf8')
    await expect(getSession('wss://x:8767')).resolves.toBeNull()
  })

  it('treats empty token string as absent', async () => {
    const raw = { sessions: { 'wss://x:8767': { paired_at: 1, token: '' } }, version: 1 }
    await fs.writeFile(storePath, JSON.stringify(raw), 'utf8')
    await expect(getSession('wss://x:8767')).resolves.toBeNull()
  })
})
