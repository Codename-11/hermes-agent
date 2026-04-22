/**
 * Remote session token storage for the desktop `--remote` flow.
 *
 * Mirrors Android's `SessionTokenStore` semantics: one bearer token per
 * `(relay URL)` key, persisted across launches so the user only pairs
 * once per device. Stored in `~/.hermes/remote-sessions.json` (mode 0600,
 * atomic tempfile → rename write). TOFU cert-pin SHA-256 lives alongside
 * the token under `cert_pin_sha256` — inlined here to avoid a second file,
 * even though Phase 3 itself doesn't write the pin (see docs/plans/
 * 2026-04-22-desktop-tui-mvp.md §cert-pin deferral).
 *
 * Fail-closed: any read/parse/permission error is treated as "no stored
 * session" and never thrown. The TUI always gets a clean `null` back; the
 * worst case is a user who paired previously gets asked to pair again.
 */

import { promises as fs } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

export interface RemoteSessionRecord {
  token: string
  serverVersion: string | null
  pairedAt: number // epoch seconds
  certPinSha256: string | null
}

interface StoredRecord {
  token: string
  server_version?: string | null
  paired_at: number
  cert_pin_sha256?: string | null
}

interface StoredFile {
  version: number
  sessions: Record<string, StoredRecord>
}

const STORE_VERSION = 1

const defaultPath = () => join(homedir(), '.hermes', 'remote-sessions.json')

/** Override for tests — restore with `setStorePath(null)`. */
let pathOverride: string | null = null

export const setStorePath = (p: string | null) => {
  pathOverride = p
}

const storePath = () => pathOverride ?? defaultPath()

const emptyFile = (): StoredFile => ({ version: STORE_VERSION, sessions: {} })

const toRecord = (raw: StoredRecord): RemoteSessionRecord => ({
  token: raw.token,
  serverVersion: raw.server_version ?? null,
  pairedAt: raw.paired_at,
  certPinSha256: raw.cert_pin_sha256 ?? null
})

const fromRecord = (r: RemoteSessionRecord): StoredRecord => ({
  token: r.token,
  server_version: r.serverVersion,
  paired_at: r.pairedAt,
  cert_pin_sha256: r.certPinSha256
})

const readFile = async (): Promise<StoredFile> => {
  try {
    const raw = await fs.readFile(storePath(), 'utf8')
    const parsed = JSON.parse(raw) as unknown

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return emptyFile()
    }

    const obj = parsed as Record<string, unknown>
    const sessions = obj.sessions

    if (!sessions || typeof sessions !== 'object' || Array.isArray(sessions)) {
      return emptyFile()
    }

    return { version: STORE_VERSION, sessions: sessions as Record<string, StoredRecord> }
  } catch {
    // ENOENT, EACCES, malformed JSON → fail closed to empty.
    return emptyFile()
  }
}

const writeFile = async (file: StoredFile): Promise<void> => {
  const path = storePath()
  const dir = dirname(path)
  await fs.mkdir(dir, { recursive: true, mode: 0o700 })

  const tmp = `${path}.tmp-${process.pid}-${Date.now()}`
  // mode 0o600 — same as Android StrongBox / server session file.
  await fs.writeFile(tmp, JSON.stringify(file, null, 2), { mode: 0o600 })
  await fs.rename(tmp, path)
}

export const getSession = async (url: string): Promise<RemoteSessionRecord | null> => {
  try {
    const file = await readFile()
    const raw = file.sessions[url]

    if (!raw || typeof raw.token !== 'string' || !raw.token) {return null}

    return toRecord(raw)
  } catch {
    return null
  }
}

export const saveSession = async (
  url: string,
  token: string,
  serverVersion: string | null,
  certPin?: string | null
): Promise<void> => {
  try {
    const file = await readFile()
    const prev = file.sessions[url]
    file.sessions[url] = fromRecord({
      token,
      serverVersion,
      pairedAt: Math.floor(Date.now() / 1000),
      certPinSha256: certPin ?? prev?.cert_pin_sha256 ?? null
    })
    await writeFile(file)
  } catch {
    // Intentionally swallow — if we can't persist, next run re-pairs.
    // The TUI never sees this failure.
  }
}

export const deleteSession = async (url: string): Promise<void> => {
  try {
    const file = await readFile()

    if (!(url in file.sessions)) {return}
    delete file.sessions[url]
    await writeFile(file)
  } catch {
    /* fail-closed */
  }
}

export const listSessions = async (): Promise<Record<string, RemoteSessionRecord>> => {
  try {
    const file = await readFile()
    const out: Record<string, RemoteSessionRecord> = {}

    for (const [url, raw] of Object.entries(file.sessions)) {
      if (raw && typeof raw.token === 'string' && raw.token) {
        out[url] = toRecord(raw)
      }
    }

    return out
  } catch {
    return {}
  }
}
