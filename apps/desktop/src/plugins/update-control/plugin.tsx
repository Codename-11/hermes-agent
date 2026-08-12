import {
  Badge,
  Button,
  cn,
  Codicon,
  type HermesPlugin,
  host,
  PALETTE_AREA,
  type PaletteContribution,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  STATUSBAR_AREAS,
  StatusDot,
  type StatusTone,
  Tip,
  useMutation,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'
import { useState } from 'react'

import { BackendUpdateActions } from './backend-actions'
import { UpdateHistory } from './history'
import {
  type BackendUpdateApplySnapshot,
  friendlyError,
  hasUpdate,
  shortSha,
  type UpdateControlApi,
  type UpdateControlStatus,
  type UpdateHistoryEntry,
  type UpdateStageSnapshot,
  type UpdateTarget
} from './model'
import { PendingChanges } from './pending-changes'
import { UpdateActions } from './update-actions'

const ROOT_KEY = ['update-control'] as const

const TARGET_OPTIONS = [
  { id: 'client', label: 'Desktop client' },
  { id: 'backend', label: 'Backend' }
] as const

type CompatibleUpdatesApi = Partial<UpdateControlApi>

interface UpdateSnapshots {
  backend: UpdateControlStatus | null
  client: UpdateControlStatus | null
}

function updatesApi(): CompatibleUpdatesApi | undefined {
  return (host as typeof host & { updates?: CompatibleUpdatesApi }).updates
}

async function readStatus(target: UpdateTarget): Promise<UpdateControlStatus | null> {
  return (await updatesApi()?.getStatus?.(target)) ?? null
}

async function readSnapshots(): Promise<UpdateSnapshots> {
  const [backend, client] = await Promise.all([readStatus('backend'), readStatus('client')])

  return { backend, client }
}

function useUpdateSnapshots() {
  return useQuery({
    queryFn: readSnapshots,
    queryKey: [...ROOT_KEY, 'snapshots'],
    refetchInterval: 30_000,
    retry: false
  })
}

function toneFor(snapshots?: UpdateSnapshots): StatusTone {
  if (!snapshots?.client && !snapshots?.backend) {
    return 'muted'
  }

  if (snapshots.client?.error || snapshots.backend?.error) {
    return 'bad'
  }

  if (hasUpdate(snapshots.client) || hasUpdate(snapshots.backend)) {
    return 'warn'
  }

  return snapshots.client?.supported || snapshots.backend?.supported ? 'good' : 'muted'
}

function statusLabel(snapshots?: UpdateSnapshots): string {
  if (!snapshots?.client && !snapshots?.backend) {
    return 'update status pending'
  }

  const pending = Number(hasUpdate(snapshots.client)) + Number(hasUpdate(snapshots.backend))

  if (pending > 0) {
    return `${pending} update${pending === 1 ? '' : 's'} available`
  }

  if (snapshots.client?.error || snapshots.backend?.error) {
    return 'update status issue'
  }

  if (!snapshots.client?.supported && !snapshots.backend?.supported) {
    return 'updates unavailable'
  }

  return 'updates current'
}

function UpdateStatusIndicator({ open }: { open: () => void }) {
  const snapshots = useUpdateSnapshots()
  const label = statusLabel(snapshots.data)

  return (
    <Tip label={`Update Control — ${label}`}>
      <button
        className={cn(
          'inline-flex h-full items-center gap-1.5 rounded-none px-1.5 text-[0.6875rem] transition-colors',
          'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)'
        )}
        onClick={open}
        type="button"
      >
        <StatusDot tone={toneFor(snapshots.data)} />
        <span>{label}</span>
      </button>
    </Tip>
  )
}

function TargetSummary({ status, target }: { status: UpdateControlStatus | null; target: UpdateTarget }) {
  const message = status?.message ?? status?.backendMessage ?? status?.reason
  const localRevision = status?.currentSha ? shortSha(status.currentSha) : status?.currentVersion ?? '—'
  const deployBranch = status?.deployBranch ?? status?.branch ?? 'Axiom deploy branch'
  const deploySha = status?.targetSha
  const upstreamBranch = status?.upstreamBranch ?? 'upstream/main'
  const upstreamSha = status?.upstreamSha

  const layers = [
    {
      detail: `${status?.upstreamBehind ?? 0} awaiting reconciliation`,
      label: 'Hermes upstream',
      ref: upstreamBranch,
      revision: shortSha(upstreamSha),
      tone: (status?.upstreamBehind ?? 0) > 0 ? 'warn' : 'good'
    },
    {
      detail: `${status?.deployBehind ?? status?.behind ?? 0} awaiting local apply`,
      label: 'Axiom',
      ref: deployBranch,
      revision: shortSha(deploySha),
      tone: (status?.deployBehind ?? status?.behind ?? 0) > 0 ? 'warn' : 'good'
    },
    {
      detail: target === 'client' ? 'This Desktop checkout' : 'Connected backend',
      label: 'Local',
      ref: status?.currentBranch ?? status?.branch ?? 'running install',
      revision: localRevision,
      tone: status?.error ? 'bad' : status?.supported ? 'good' : 'muted'
    }
  ] as const

  return (
    <section aria-label={`${target} status`} className="py-4">
      <div className="grid gap-px overflow-hidden rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-stroke-tertiary) md:grid-cols-3">
        {layers.map((layer, index) => (
          <div className="min-w-0 bg-(--ui-surface-background) p-3.5" key={layer.label}>
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2">
                <StatusDot tone={layer.tone} />
                <h2 className="truncate text-xs font-semibold text-(--ui-text-primary)">{layer.label}</h2>
              </div>
              <span className="text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)">
                {index + 1} / 3
              </span>
            </div>
            <p className="mt-3 truncate text-xs text-(--ui-text-secondary)">{layer.ref}</p>
            <p className="mt-1 font-mono text-[0.6875rem] text-(--ui-text-tertiary)">{layer.revision}</p>
            <p className="mt-2 text-[0.6875rem] leading-4 text-(--ui-text-quaternary)">{layer.detail}</p>
          </div>
        ))}
      </div>
      {message ? <p className="mt-3 text-xs leading-5 text-(--ui-text-tertiary)">{message}</p> : null}
    </section>
  )
}

function UpdateControlPane() {
  const queryClient = useQueryClient()
  const [target, setTarget] = useState<UpdateTarget>('client')
  const [actionError, setActionError] = useState<string | null>(null)
  const api = updatesApi()

  const statusQuery = useQuery({
    queryFn: () => readStatus(target),
    queryKey: [...ROOT_KEY, 'status', target],
    retry: false
  })

  const stageQuery = useQuery({
    queryFn: async () => {
      const stage = (await api?.getStage?.()) ?? null

      if (stage && stage.state !== 'preparing') {
        void queryClient.invalidateQueries({ queryKey: [...ROOT_KEY, 'history'] })
      }

      return stage
    },
    queryKey: [...ROOT_KEY, 'stage'],
    refetchInterval: query =>
      (query.state.data as UpdateStageSnapshot | null)?.state === 'preparing' ? 2_000 : false,
    retry: false
  })

  const backendApplyQuery = useQuery({
    enabled: target === 'backend',
    queryFn: async () => (await api?.getBackendApply?.()) ?? null,
    queryKey: [...ROOT_KEY, 'backend-apply'],
    refetchInterval: target === 'backend' ? 1_000 : false,
    retry: false
  })

  const historyQuery = useQuery({
    queryFn: async () => (await api?.getHistory?.()) ?? [],
    queryKey: [...ROOT_KEY, 'history'],
    retry: false
  })

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ROOT_KEY })

  const lifecycle = useMutation({
    mutationFn: async (action: 'discard' | 'prepare' | 'restart') => {
      setActionError(null)

      if (action === 'prepare' && api?.prepare) {
        return api.prepare()
      }

      if (action === 'discard' && api?.discardStage) {
        return api.discardStage()
      }

      if (action === 'restart' && api?.restartAndApply) {
        return api.restartAndApply()
      }

      throw new Error('Staged update controls are unavailable in this Desktop build.')
    },
    onError: error => setActionError(friendlyError(error)),
    onSuccess: (result, action) => {
      if (action === 'prepare') {
        queryClient.setQueryData([...ROOT_KEY, 'stage'], result)
      }
    },
    onSettled: (_result, _error, action) => {
      if (action === 'prepare') {
        void queryClient.invalidateQueries({ queryKey: [...ROOT_KEY, 'snapshots'] })
        void queryClient.invalidateQueries({ queryKey: [...ROOT_KEY, 'status'] })

        return
      }

      invalidate()
    }
  })

  const refresh = useMutation({
    mutationFn: async () => {
      setActionError(null)

      if (api?.refresh) {
        await api.refresh(target)
      } else {
        await statusQuery.refetch()
      }
    },
    onError: error => setActionError(friendlyError(error)),
    onSettled: invalidate
  })

  const backendUpdate = useMutation({
    mutationFn: async () => {
      setActionError(null)

      if (!api?.applyBackend) {
        throw new Error('Backend update controls are unavailable in this Desktop build.')
      }

      return api.applyBackend()
    },
    onError: error => setActionError(friendlyError(error)),
    onSettled: invalidate
  })

  const status = statusQuery.data ?? null
  const stage = (stageQuery.data ?? null) as UpdateStageSnapshot | null
  const history = (historyQuery.data ?? []) as UpdateHistoryEntry[]
  const backendApply = (backendApplyQuery.data ?? null) as BackendUpdateApplySnapshot | null
  const deployCommits = stage?.commits ?? status?.deployCommits ?? status?.commits ?? []
  const upstreamCommits = status?.upstreamCommits ?? []
  const queryError = statusQuery.error || stageQuery.error || historyQuery.error

  const primaryLoading =
    statusQuery.isPending ||
    (target === 'client' && stageQuery.isPending) ||
    (target === 'backend' && backendApplyQuery.isPending)

  return (
    <div className="h-full min-h-0 overflow-y-auto">
      <main className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 sm:py-7">
        <header>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Codicon className="text-(--ui-text-tertiary)" name="cloud-download" size="1.1rem" />
              <h1 className="text-lg font-semibold tracking-tight text-(--ui-text-primary)">Update Control</h1>
              <Badge>
                {primaryLoading ? 'loading' : target === 'client' && stage?.state ? stage.state : hasUpdate(status) ? 'available' : 'current'}
              </Badge>
            </div>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-(--ui-text-tertiary)">
              Prepare and verify updates in the background, then finish through a safe restart handoff.
            </p>
          </div>
        </header>

        <div className="mt-5 flex flex-col gap-3 border-t border-(--ui-stroke-tertiary) pt-4 sm:flex-row sm:items-center sm:justify-between">
          <SegmentedControl onChange={setTarget} options={TARGET_OPTIONS} value={target} />
          <Button disabled={refresh.isPending} onClick={() => refresh.mutate()} size="sm" variant="ghost">
            <Codicon className={cn(refresh.isPending && 'animate-spin')} name="refresh" size="0.8rem" />
            {refresh.isPending ? 'Refreshing…' : 'Refresh'}
          </Button>
        </div>

        {statusQuery.isPending ? (
          <p className="py-6 text-xs text-(--ui-text-tertiary)" role="status">Loading update status…</p>
        ) : statusQuery.isSuccess ? (
          <TargetSummary status={status} target={target} />
        ) : null}

        {actionError || queryError ? (
          <div className="border-l-2 border-destructive pl-3 text-xs leading-5 text-(--ui-text-secondary)">
            {actionError ?? friendlyError(queryError)}
          </div>
        ) : null}

        {primaryLoading ? null : target === 'client' ? (
          <UpdateActions
            busy={lifecycle.isPending || refresh.isPending}
            onDiscard={() => lifecycle.mutate('discard')}
            onPrepare={() => lifecycle.mutate('prepare')}
            onRefresh={() => refresh.mutate()}
            onRestart={() => lifecycle.mutate('restart')}
            stage={stage}
            status={status}
          />
        ) : (
          <BackendUpdateActions
            apply={backendApply}
            busy={backendUpdate.isPending || refresh.isPending}
            onApply={() => backendUpdate.mutate()}
            onRefresh={() => refresh.mutate()}
            status={status}
          />
        )}

        {stage?.fallbackCommand || status?.fallbackCommand ? (
          <div className="mt-4 border-l-2 border-(--ui-accent) pl-3 text-xs leading-5 text-(--ui-text-tertiary)">
            Compatibility command:{' '}
            <code className="select-all font-mono text-(--ui-text-secondary)">
              {stage?.fallbackCommand || status?.fallbackCommand}
            </code>
          </div>
        ) : null}

        {!primaryLoading && statusQuery.isSuccess ? (
          <div className="mt-6">
            <PendingChanges
              commits={deployCommits}
              description={`${status?.deployBehind ?? deployCommits.length} commit${(status?.deployBehind ?? deployCommits.length) === 1 ? '' : 's'} between Local and Axiom`}
              filesChanged={stage?.filesChanged}
              heading="Axiom changes"
              shortstat={stage?.shortstat}
            />
          </div>
        ) : null}
        {!primaryLoading && statusQuery.isSuccess ? (
          <div className="mt-6">
            <PendingChanges
              commits={upstreamCommits}
              description={`${status?.upstreamBehind ?? upstreamCommits.length} commit${(status?.upstreamBehind ?? upstreamCommits.length) === 1 ? '' : 's'} between Axiom and Hermes upstream${upstreamCommits.length > 0 && (status?.upstreamBehind ?? 0) > upstreamCommits.length ? ` · showing latest ${upstreamCommits.length}` : ''}`}
              heading="Hermes upstream history"
            />
          </div>
        ) : null}
        <div className="mt-6">
          {historyQuery.isPending ? (
            <p className="border-t border-(--ui-stroke-tertiary) pt-5 text-xs text-(--ui-text-tertiary)" role="status">
              Loading update history…
            </p>
          ) : historyQuery.isSuccess ? (
            <UpdateHistory entries={history} />
          ) : null}
        </div>

        {!api?.prepare ? (
          <p className="mt-6 border-t border-(--ui-stroke-tertiary) pt-4 text-xs leading-5 text-(--ui-text-quaternary)">
            Client staging controls require a newer Desktop core.
          </p>
        ) : null}
      </main>
    </div>
  )
}

const plugin: HermesPlugin = {
  id: 'update-control',
  name: 'Update Control',
  defaultEnabled: true,
  register(ctx) {
    const open = () => ctx.panes.reveal('panel')

    ctx.registerMany([
      {
        id: 'panel',
        area: 'panes',
        title: 'Update Control',
        data: { closeBehavior: 'dismiss', placement: 'main' },
        render: () => <UpdateControlPane />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { codicon: 'cloud-download', label: 'Update Control', onSelect: open } satisfies SidebarNavContribution
      },
      {
        id: 'status',
        area: STATUSBAR_AREAS.right,
        order: 85,
        render: () => <UpdateStatusIndicator open={open} />
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'update-control.open',
          label: 'Update Control: Open',
          keywords: ['update', 'version', 'client', 'backend', 'branch', 'prepare', 'history'],
          run: open
        } satisfies PaletteContribution
      }
    ])
  }
}

export default plugin
