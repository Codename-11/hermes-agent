export type UpdateTarget = 'backend' | 'client'

export interface UpdateSummary {
  supported?: boolean
  updateAvailable?: boolean
  behind?: number
  dirty?: boolean
  error?: string
  currentSha?: string
  currentVersion?: string
  targetSha?: string
}

export interface UpdateControlStatus extends UpdateSummary {
  branch?: string
  currentBranch?: string
  reason?: string
  message?: string
  backendMessage?: string
  commits?: UpdateCommit[]
  fetchedAt?: number
  upstreamBranch?: string
  upstreamBehind?: number
  deployBranch?: string
  deployBehind?: number
  fallbackCommand?: string
}

type Awaitable<T> = Promise<T> | T

/** Narrow renderer capability the core update lane wires onto host.updates. */
export interface UpdateControlApi {
  getStatus(target: UpdateTarget): Awaitable<UpdateControlStatus | null>
  getStage(): Awaitable<UpdateStageSnapshot | null>
  getHistory(): Awaitable<UpdateHistoryEntry[]>
  refresh(target: UpdateTarget): Awaitable<UpdateControlStatus | null | void>
  prepare(): Awaitable<unknown>
  discardStage(): Awaitable<unknown>
  restartAndApply(): Awaitable<unknown>
  openNative(): void
}

export interface UpdateCommit {
  sha: string
  summary: string
  author?: string
  at?: number
  additions?: number
  deletions?: number
  filesChanged?: number
  shortstat?: string
}

export type ChangeCategoryKey = 'docs' | 'features' | 'fixes' | 'other' | 'performance' | 'refactors'

export interface CategorizedCommit extends UpdateCommit {
  subject: string
}

export interface ChangeCategory {
  commits: CategorizedCommit[]
  count: number
  key: ChangeCategoryKey
  label: string
}

export type PreparationState = 'available' | 'failed' | 'invalid' | 'preparing' | 'ready'
export type PreparationAction = 'prepare' | 'refresh' | 'restartAndApply'

export interface UpdateStageSnapshot {
  supported?: boolean
  state: PreparationState
  phase?: string
  percent?: number | null
  message?: string
  error?: string
  invalidationReason?: string
  currentSha?: string
  targetSha?: string
  branch?: string
  startedAt?: number
  preparedAt?: number
  commits?: UpdateCommit[]
  shortstat?: string
  filesChanged?: number
  fallbackCommand?: string
}

export type UpdateHistoryResult = 'cancelled' | 'completed' | 'failed'

export interface UpdateHistoryEntry {
  id: string
  result: UpdateHistoryResult
  branch?: string
  startedAt?: number
  finishedAt?: number
  fromSha?: string
  toSha?: string
  message?: string
  error?: string
  phase?: string
  briefPath?: string
  logPath?: string
  commits?: UpdateCommit[]
  shortstat?: string
  filesChanged?: number
}

export interface PreparationView {
  action: PreparationAction | null
  canDiscard: boolean
  description: string
  diagnostic: string | null
  state: PreparationState
  title: string
  tone: 'bad' | 'good' | 'muted' | 'warn'
}

const CATEGORY_DEFINITIONS: ReadonlyArray<{
  key: ChangeCategoryKey
  label: string
  prefixes: readonly string[]
}> = [
  { key: 'features', label: 'Features', prefixes: ['feat'] },
  { key: 'fixes', label: 'Fixes', prefixes: ['fix'] },
  { key: 'performance', label: 'Performance', prefixes: ['perf'] },
  { key: 'refactors', label: 'Refactors', prefixes: ['refactor'] },
  { key: 'docs', label: 'Docs', prefixes: ['docs'] },
  { key: 'other', label: 'Other', prefixes: [] }
]

const CONVENTIONAL_SUBJECT = /^([a-z][a-z0-9-]*)(?:\([^)]*\))?!?:\s*(.+)$/i

export function hasUpdate(status: UpdateSummary | null | undefined): boolean {
  return status?.supported === true && (status.updateAvailable === true || (status.behind ?? 0) > 0)
}

export function shortSha(value?: null | string): string {
  return value ? value.slice(0, 8) : '—'
}

export function friendlyError(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }

  if (typeof error === 'string' && error.trim()) {
    return error
  }

  return 'Update information is unavailable right now.'
}

export function categorizeCommits(commits: readonly UpdateCommit[] = []): ChangeCategory[] {
  const grouped = new Map<ChangeCategoryKey, CategorizedCommit[]>(
    CATEGORY_DEFINITIONS.map(category => [category.key, []])
  )

  for (const commit of commits) {
    const match = CONVENTIONAL_SUBJECT.exec(commit.summary.trim())
    const prefix = match?.[1]?.toLowerCase()

    const definition =
      CATEGORY_DEFINITIONS.find(category => prefix && category.prefixes.includes(prefix)) ??
      CATEGORY_DEFINITIONS[CATEGORY_DEFINITIONS.length - 1]

    grouped.get(definition.key)?.push({ ...commit, subject: match?.[2]?.trim() || commit.summary })
  }

  return CATEGORY_DEFINITIONS.map(({ key, label }) => {
    const categoryCommits = grouped.get(key) ?? []

    return { commits: categoryCommits, count: categoryCommits.length, key, label }
  })
}

export function derivePreparationView(
  status: UpdateSummary | null | undefined,
  stage: UpdateStageSnapshot | null | undefined
): PreparationView {
  const statusDiagnostic = !status?.supported
    ? 'This install method does not support staged updates.'
    : status.dirty
      ? 'Local changes are present. Preparation may require cleanup before it can continue.'
      : status.error || null

  if (stage?.state === 'preparing') {
    return {
      action: null,
      canDiscard: false,
      description: stage.message || `Preparing${stage.phase ? ` · ${stage.phase}` : ''}`,
      diagnostic: statusDiagnostic,
      state: 'preparing',
      title: 'Preparing update',
      tone: 'warn'
    }
  }

  if (stage?.state === 'ready') {
    return {
      action: 'restartAndApply',
      canDiscard: true,
      description: 'Preparation is complete. Hermes will close, finish the update, and reopen.',
      diagnostic: statusDiagnostic,
      state: 'ready',
      title: 'Ready to restart',
      tone: 'good'
    }
  }

  if (stage?.state === 'invalid') {
    return {
      action: 'prepare',
      canDiscard: true,
      description: 'The prepared update no longer matches the live installation. Prepare it again.',
      diagnostic: stage.invalidationReason || statusDiagnostic,
      state: 'invalid',
      title: 'Stage invalidated',
      tone: 'bad'
    }
  }

  if (stage?.state === 'failed') {
    return {
      action: 'prepare',
      canDiscard: true,
      description: 'Preparation stopped safely. The running installation was not changed.',
      diagnostic: stage.error || stage.message || statusDiagnostic,
      state: 'failed',
      title: 'Preparation failed',
      tone: 'bad'
    }
  }

  if (hasUpdate(status)) {
    if (stage?.supported === false) {
      return {
        action: null,
        canDiscard: false,
        description: stage.message || 'Staged updates are unavailable on this platform.',
        diagnostic: stage.message || statusDiagnostic,
        state: 'available',
        title: 'Staging unavailable',
        tone: 'muted'
      }
    }

    return {
      action: 'prepare',
      canDiscard: false,
      description: 'Build and verify the target away from the live installation while you keep working.',
      diagnostic: statusDiagnostic,
      state: 'available',
      title: 'Update available',
      tone: 'warn'
    }
  }

  return {
    action: 'refresh',
    canDiscard: false,
    description: status?.supported
      ? 'This target is current. Refresh to check for new changes.'
      : 'Use the compatibility updater or update this installation manually.',
    diagnostic: statusDiagnostic,
    state: 'available',
    title: status?.supported ? 'Up to date' : 'Staging unavailable',
    tone: status?.supported ? 'good' : 'muted'
  }
}

function formatDuration(startedAt?: number, finishedAt?: number): string {
  if (startedAt == null || finishedAt == null || finishedAt < startedAt) {
    return '—'
  }

  const seconds = Math.max(0, Math.floor((finishedAt - startedAt) / 1_000))
  const minutes = Math.floor(seconds / 60)
  const remaining = seconds % 60

  return minutes > 0 ? `${minutes}m ${remaining}s` : `${remaining}s`
}

export function formatHistoryEntry(entry: UpdateHistoryEntry): {
  duration: string
  range: string
  resultLabel: string
  summary: string
  tone: 'bad' | 'good' | 'muted'
} {
  const resultLabel = entry.result === 'completed' ? 'Completed' : entry.result === 'failed' ? 'Failed' : 'Cancelled'

  return {
    duration: formatDuration(entry.startedAt, entry.finishedAt),
    range: entry.fromSha && entry.toSha ? `${shortSha(entry.fromSha)} → ${shortSha(entry.toSha)}` : 'Unknown range',
    resultLabel,
    summary: entry.error || entry.message || (entry.result === 'completed' ? 'Update completed successfully.' : resultLabel),
    tone: entry.result === 'completed' ? 'good' : entry.result === 'failed' ? 'bad' : 'muted'
  }
}
