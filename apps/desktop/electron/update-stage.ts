import { createHash, randomBytes } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const UPDATE_STAGE_SCHEMA_VERSION = 1 as const

export interface DesktopUpdateStageManifest {
  schemaVersion: typeof UPDATE_STAGE_SCHEMA_VERSION
  branch: string
  baseSha: string
  targetSha: string
  installRoot: string
  artifactPath: string
  artifactSha256: string
  createdAt: number
}

export type DesktopUpdateStagePhase =
  | 'idle'
  | 'fetching'
  | 'preparing-dependencies'
  | 'building'
  | 'verifying'
  | 'ready'
  | 'applying'
  | 'invalidated'
  | 'failed'

export interface DesktopUpdateStageProgress {
  phase: DesktopUpdateStagePhase
  message?: string
  percent?: number
  logPath?: string
}

export interface DesktopUpdateStageStatus extends DesktopUpdateStageProgress {
  supported: boolean
  manifest?: DesktopUpdateStageManifest
  reason?: DesktopUpdateStageInvalidReason | 'malformed' | 'missing'
}

export interface DesktopUpdatePrepareResult {
  ok: boolean
  status: DesktopUpdateStageStatus
  error?: string
}

export interface DesktopUpdateDiscardResult {
  ok: boolean
  discarded: boolean
  error?: string
}

export interface DesktopUpdateRestartResult {
  ok: boolean
  applying?: boolean
  fallback?: boolean
  error?: string
}

export type DesktopUpdateStageInvalidReason =
  | 'branch-changed'
  | 'head-changed'
  | 'target-changed'
  | 'install-root-changed'
  | 'missing-artifact'
  | 'artifact-hash-mismatch'

export interface DesktopUpdateStageLiveState {
  branch: string
  headSha: string
  installRoot: string
  targetSha: string
}

export type DesktopUpdateStageValidation =
  { valid: true; manifest: DesktopUpdateStageManifest } | { valid: false; reason: DesktopUpdateStageInvalidReason }

export interface UpdateStageValidationDeps {
  fileExists?: (candidate: string) => boolean
  sha256File?: (candidate: string) => string
}

export interface UpdateStageReadDeps {
  readFile?: (filePath: string) => string | null
}

export type DesktopUpdateStageReadResult =
  { kind: 'missing' } | { kind: 'malformed'; error: string } | { kind: 'ready'; manifest: DesktopUpdateStageManifest }

export interface UpdateStageWriteDeps {
  mkdir?: (dirPath: string) => void
  writeFile?: (filePath: string, data: string) => void
  rename?: (from: string, to: string) => void
  remove?: (filePath: string) => void
  randomToken?: () => string
}

const GIT_SHA_RE = /^[0-9a-f]{40}$/i
const SHA256_RE = /^[0-9a-f]{64}$/i

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function requireNonEmptyString(value: unknown, name: string): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`Malformed update stage manifest: ${name} must be a non-empty string`)
  }

  return value
}

export function parseUpdateStageManifest(text: string): DesktopUpdateStageManifest {
  let value: unknown

  try {
    value = JSON.parse(text)
  } catch {
    throw new Error('Malformed update stage manifest JSON')
  }

  if (!isObject(value) || value.schemaVersion !== UPDATE_STAGE_SCHEMA_VERSION) {
    throw new Error('Malformed update stage manifest: unsupported schema version')
  }

  const branch = requireNonEmptyString(value.branch, 'branch')
  const baseSha = requireNonEmptyString(value.baseSha, 'baseSha')
  const targetSha = requireNonEmptyString(value.targetSha, 'targetSha')
  const installRoot = requireNonEmptyString(value.installRoot, 'installRoot')
  const artifactPath = requireNonEmptyString(value.artifactPath, 'artifactPath')
  const artifactSha256 = requireNonEmptyString(value.artifactSha256, 'artifactSha256')

  if (!GIT_SHA_RE.test(baseSha) || !GIT_SHA_RE.test(targetSha)) {
    throw new Error('Malformed update stage manifest: invalid Git hash')
  }

  if (!SHA256_RE.test(artifactSha256)) {
    throw new Error('Malformed update stage manifest: invalid artifact hash')
  }

  if (typeof value.createdAt !== 'number' || !Number.isFinite(value.createdAt) || value.createdAt < 0) {
    throw new Error('Malformed update stage manifest: invalid createdAt')
  }

  return {
    schemaVersion: UPDATE_STAGE_SCHEMA_VERSION,
    branch,
    baseSha: baseSha.toLowerCase(),
    targetSha: targetSha.toLowerCase(),
    installRoot,
    artifactPath,
    artifactSha256: artifactSha256.toLowerCase(),
    createdAt: value.createdAt
  }
}

function defaultFileExists(candidate: string): boolean {
  try {
    return fs.statSync(candidate).isFile()
  } catch {
    return false
  }
}

function defaultSha256File(candidate: string): string {
  return createHash('sha256').update(fs.readFileSync(candidate)).digest('hex')
}

export function validateUpdateStageManifest(
  manifest: DesktopUpdateStageManifest,
  live: DesktopUpdateStageLiveState,
  deps: UpdateStageValidationDeps = {}
): DesktopUpdateStageValidation {
  if (manifest.branch !== live.branch) {
    return { valid: false, reason: 'branch-changed' }
  }

  if (manifest.baseSha.toLowerCase() !== live.headSha.toLowerCase()) {
    return { valid: false, reason: 'head-changed' }
  }

  if (manifest.targetSha.toLowerCase() !== live.targetSha.toLowerCase()) {
    return { valid: false, reason: 'target-changed' }
  }

  if (path.resolve(manifest.installRoot) !== path.resolve(live.installRoot)) {
    return { valid: false, reason: 'install-root-changed' }
  }

  const fileExists = deps.fileExists ?? defaultFileExists

  if (!fileExists(manifest.artifactPath)) {
    return { valid: false, reason: 'missing-artifact' }
  }

  const actualHash = (deps.sha256File ?? defaultSha256File)(manifest.artifactPath)

  if (!SHA256_RE.test(actualHash) || actualHash.toLowerCase() !== manifest.artifactSha256.toLowerCase()) {
    return { valid: false, reason: 'artifact-hash-mismatch' }
  }

  return { valid: true, manifest }
}

function defaultReadFile(filePath: string): string | null {
  try {
    return fs.readFileSync(filePath, 'utf8')
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return null
    }

    throw error
  }
}

export function readUpdateStageManifest(
  filePath: string,
  deps: UpdateStageReadDeps = {}
): DesktopUpdateStageReadResult {
  const text = (deps.readFile ?? defaultReadFile)(filePath)

  if (text === null) {
    return { kind: 'missing' }
  }

  try {
    return { kind: 'ready', manifest: parseUpdateStageManifest(text) }
  } catch (error) {
    return { kind: 'malformed', error: error instanceof Error ? error.message : String(error) }
  }
}

export function writeUpdateStageManifestAtomic(
  filePath: string,
  manifest: DesktopUpdateStageManifest,
  deps: UpdateStageWriteDeps = {}
): void {
  const normalized = parseUpdateStageManifest(JSON.stringify(manifest))
  const mkdir = deps.mkdir ?? (dirPath => fs.mkdirSync(dirPath, { recursive: true }))
  const writeFile = deps.writeFile ?? ((target, data) => fs.writeFileSync(target, data, 'utf8'))
  const rename = deps.rename ?? fs.renameSync
  const remove = deps.remove ?? (target => fs.rmSync(target, { force: true }))
  const token = (deps.randomToken ?? (() => randomBytes(8).toString('hex')))()
  const tempPath = `${filePath}.${token}.tmp`

  mkdir(path.dirname(filePath))

  try {
    writeFile(tempPath, `${JSON.stringify(normalized, null, 2)}\n`)
    rename(tempPath, filePath)
  } catch (error) {
    try {
      remove(tempPath)
    } catch {
      // Best-effort cleanup; preserve the authoritative write error.
    }

    throw error
  }
}
