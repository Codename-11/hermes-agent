import { randomBytes } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const UPDATE_HISTORY_LIMIT = 50

export type DesktopUpdateHistoryPhase = 'prepare' | 'apply'
export type DesktopUpdateHistoryResult = 'completed' | 'failed' | 'cancelled'
export type DesktopUpdateChangeCategory = 'features' | 'fixes' | 'performance' | 'refactors' | 'docs' | 'other'

export interface DesktopUpdateHistoryCommit {
  sha: string
  subject: string
  author: string
  at?: number
  category?: DesktopUpdateChangeCategory
}

export interface DesktopUpdateHistoryEntry {
  id: string
  at: number
  phase: DesktopUpdateHistoryPhase
  result: DesktopUpdateHistoryResult
  branch?: string
  baseSha?: string
  targetSha?: string
  message?: string
  commits?: DesktopUpdateHistoryCommit[]
  shortstat?: string
  filesChanged?: number
  briefPath?: string
  logPath?: string
}

export interface UpdateHistoryFs {
  readFile?: (filePath: string) => string | null
  mkdir?: (dirPath: string) => void
  writeFile?: (filePath: string, data: string) => void
  rename?: (from: string, to: string) => void
  remove?: (filePath: string) => void
  randomToken?: () => string
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

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function parseCommit(value: unknown): DesktopUpdateHistoryCommit | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }

  const item = value as Record<string, unknown>

  if (typeof item.sha !== 'string' || typeof item.subject !== 'string' || typeof item.author !== 'string') {
    return null
  }

  const commit: DesktopUpdateHistoryCommit = {
    sha: item.sha,
    subject: item.subject,
    author: item.author
  }

  if (typeof item.at === 'number' && Number.isFinite(item.at)) {
    commit.at = item.at
  }

  if (['features', 'fixes', 'performance', 'refactors', 'docs', 'other'].includes(String(item.category))) {
    commit.category = item.category as DesktopUpdateChangeCategory
  }

  return commit
}

function parseEntry(value: unknown): DesktopUpdateHistoryEntry | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }

  const item = value as Record<string, unknown>

  if (typeof item.id !== 'string' || item.id.length === 0) {
    return null
  }

  if (typeof item.at !== 'number' || !Number.isFinite(item.at)) {
    return null
  }

  if (item.phase !== 'prepare' && item.phase !== 'apply') {
    return null
  }

  if (item.result !== 'completed' && item.result !== 'failed' && item.result !== 'cancelled') {
    return null
  }

  const entry: DesktopUpdateHistoryEntry = {
    id: item.id,
    at: item.at,
    phase: item.phase,
    result: item.result
  }

  for (const key of ['branch', 'baseSha', 'targetSha', 'message', 'shortstat', 'briefPath', 'logPath'] as const) {
    const parsed = optionalString(item[key])

    if (parsed !== undefined) {
      entry[key] = parsed
    }
  }

  if (typeof item.filesChanged === 'number' && Number.isInteger(item.filesChanged) && item.filesChanged >= 0) {
    entry.filesChanged = item.filesChanged
  }

  if (Array.isArray(item.commits)) {
    entry.commits = item.commits
      .map(parseCommit)
      .filter((commit): commit is DesktopUpdateHistoryCommit => commit !== null)
  }

  return entry
}

export function readUpdateHistory(
  filePath: string,
  deps: Pick<UpdateHistoryFs, 'readFile'> = {}
): DesktopUpdateHistoryEntry[] {
  const text = (deps.readFile ?? defaultReadFile)(filePath)

  if (text === null) {
    return []
  }

  try {
    const value: unknown = JSON.parse(text)

    if (!Array.isArray(value)) {
      return []
    }

    return value
      .map(parseEntry)
      .filter((entry): entry is DesktopUpdateHistoryEntry => entry !== null)
      .sort((left, right) => right.at - left.at)
      .slice(0, UPDATE_HISTORY_LIMIT)
  } catch {
    return []
  }
}

export function appendUpdateHistory(
  filePath: string,
  entry: DesktopUpdateHistoryEntry,
  deps: UpdateHistoryFs = {}
): DesktopUpdateHistoryEntry[] {
  const normalized = parseEntry(entry)

  if (!normalized) {
    throw new Error('Invalid update history entry')
  }

  const history = [normalized, ...readUpdateHistory(filePath, deps).filter(item => item.id !== normalized.id)].slice(
    0,
    UPDATE_HISTORY_LIMIT
  )

  const mkdir = deps.mkdir ?? (dirPath => fs.mkdirSync(dirPath, { recursive: true }))
  const writeFile = deps.writeFile ?? ((target, data) => fs.writeFileSync(target, data, 'utf8'))
  const rename = deps.rename ?? fs.renameSync
  const remove = deps.remove ?? (target => fs.rmSync(target, { force: true }))
  const token = (deps.randomToken ?? (() => randomBytes(8).toString('hex')))()
  const tempPath = `${filePath}.${token}.tmp`

  mkdir(path.dirname(filePath))

  try {
    writeFile(tempPath, `${JSON.stringify(history, null, 2)}\n`)
    rename(tempPath, filePath)
  } catch (error) {
    try {
      remove(tempPath)
    } catch {
      // Preserve the original append failure.
    }

    throw error
  }

  return history
}
