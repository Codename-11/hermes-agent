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
  deployCommits?: UpdateCommit[]
  fetchedAt?: number
  upstreamBranch?: string
  upstreamSha?: string
  upstreamBehind?: number
  upstreamCommits?: UpdateCommit[]
  retainedUpstreamHandoff?: boolean
  deployBranch?: string
  deployBehind?: number
  fallbackCommand?: string
}

type Awaitable<T> = Promise<T> | T

/** Narrow renderer capability the core update lane wires onto host.updates. */
export interface UpdateControlApi {
  getStatus(target: UpdateTarget): Awaitable<UpdateControlStatus | null>
  getBackendApply(): BackendUpdateApplySnapshot
  getStage(): Awaitable<UpdateStageSnapshot | null>
  getHistory(): Awaitable<UpdateHistoryEntry[]>
  refresh(target: UpdateTarget): Awaitable<UpdateControlStatus | null | void>
  prepare(): Awaitable<UpdateStageSnapshot | null>
  cancelPreparation(): Awaitable<unknown>
  discardStage(): Awaitable<unknown>
  restartAndApply(): Awaitable<unknown>
  standardUpdate(): Awaitable<unknown>
  applyBackend(): Awaitable<unknown>
  syncUpstream(): Awaitable<UpstreamSyncSnapshot>
}

export interface UpstreamSyncSnapshot {
  ok: boolean
  state: 'completed' | 'failed' | 'handoff'
  message: string
  error?: string
  branch?: string
  reconciled?: number
  targetSha?: string
  worktree?: string
  reportPath?: string
  output?: string
}

export const canSyncUpstream = (
  status: UpdateControlStatus | null,
  stage: UpdateStageSnapshot | null = null
) => stage == null && ((status?.upstreamBehind ?? 0) > 0 || status?.retainedUpstreamHandoff === true)

export interface BackendUpdateApplySnapshot {
  applying: boolean
  stage: string
  message: string
  percent: number | null
  error: string | null
  command: string | null
  output?: string
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
export type CommitScopeKey = 'cli-backend' | 'desktop' | 'other' | 'skills-docs'

export interface CategorizedCommit extends UpdateCommit {
  subject: string
}

export interface PresentedCommit extends CategorizedCommit {
  category: ChangeCategoryKey
  categoryLabel: string
  scope: CommitScopeKey
  scopeLabel: string
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
  checkedAt?: number
  ownerActive?: boolean
  cancellable?: boolean
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
  output?: string
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

const CONVENTIONAL_SUBJECT = /^([a-z][a-z0-9-]*)(?:\(([^)]*)\))?!?:\s*(.+)$/i

const SCOPE_LABELS: Record<CommitScopeKey, string> = {
  'cli-backend': 'CLI & backend',
  desktop: 'Desktop',
  other: 'Other',
  'skills-docs': 'Skills & docs'
}

export function presentCommit(commit: UpdateCommit): PresentedCommit {
  const match = CONVENTIONAL_SUBJECT.exec(commit.summary.trim())
  const prefix = match?.[1]?.toLowerCase()
  const conventionalScope = match?.[2]?.toLowerCase() ?? ''

  const definition =
    CATEGORY_DEFINITIONS.find(category => prefix && category.prefixes.includes(prefix)) ??
    CATEGORY_DEFINITIONS[CATEGORY_DEFINITIONS.length - 1]

  const scopeTokens = conventionalScope.split(/[,/\s-]+/).filter(Boolean)

  let scope: CommitScopeKey = 'other'

  if (scopeTokens.some(token => ['desktop', 'electron', 'renderer', 'ui', 'ux'].includes(token))) {
    scope = 'desktop'
  } else if (
    scopeTokens.some(token => ['agent', 'api', 'backend', 'cli', 'gateway', 'model', 'provider', 'proxy', 'terminal'].includes(token))
  ) {
    scope = 'cli-backend'
  } else if (prefix === 'docs' || scopeTokens.some(token => ['docs', 'skill', 'skills', 'website'].includes(token))) {
    scope = 'skills-docs'
  }

  return {
    ...commit,
    category: definition.key,
    categoryLabel: definition.label,
    scope,
    scopeLabel: SCOPE_LABELS[scope],
    subject: match?.[3]?.trim() || commit.summary
  }
}

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
    const presented = presentCommit(commit)
    grouped.get(presented.category)?.push(presented)
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
