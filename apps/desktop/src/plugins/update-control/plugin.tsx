import {
  Badge,
  Button,
  cn,
  Codicon,
  type HermesPlugin,
  host,
  PALETTE_AREA,
  type PaletteContribution,
  PANES_AREA,
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

import { UpdateHistory } from './history'
import {
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

const PANE_ID = 'panel'
const ROOT_KEY = ['update-control'] as const

const TARGET_OPTIONS = [
  { id: 'client', label: 'Desktop client' },
  { id: 'backend', label: 'Backend' }
] as const

type CompatibleUpdatesApi = Partial<UpdateControlApi> & { open?: () => void }

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
  const branch = status?.currentBranch ?? status?.branch ?? '—'
  const current = status?.currentSha ?? status?.currentVersion
  const message = status?.message ?? status?.backendMessage ?? status?.reason

  return (
    <section aria-label={`${target} status`} className="py-4">
      <div className="grid grid-cols-2 gap-x-5 gap-y-3 sm:grid-cols-4">
        <div>
          <p className="text-[0.625rem] font-semibold uppercase tracking-wide text-(--ui-text-quaternary)">Current</p>
          <p className="mt-1 truncate font-mono text-xs text-(--ui-text-primary)">{shortSha(current)}</p>
        </div>
        <div>
          <p className="text-[0.625rem] font-semibold uppercase tracking-wide text-(--ui-text-quaternary)">Target</p>
          <p className="mt-1 truncate font-mono text-xs text-(--ui-text-primary)">{shortSha(status?.targetSha)}</p>
        </div>
        <div>
          <p className="text-[0.625rem] font-semibold uppercase tracking-wide text-(--ui-text-quaternary)">Branch</p>
          <p className="mt-1 truncate text-xs text-(--ui-text-primary)">{branch}</p>
        </div>
        <div>
          <p className="text-[0.625rem] font-semibold uppercase tracking-wide text-(--ui-text-quaternary)">Behind</p>
          <p className="mt-1 text-xs text-(--ui-text-primary)">{status?.supported ? (status.behind ?? 0) : '—'}</p>
        </div>
      </div>
      {message ? <p className="mt-3 text-xs leading-5 text-(--ui-text-tertiary)">{message}</p> : null}
      {status?.deployBehind != null || status?.upstreamBehind != null ? (
        <p className="mt-2 text-[0.6875rem] text-(--ui-text-quaternary)">
          {[
            status.deployBehind != null ? `${status.deployBranch || 'deploy'}: ${status.deployBehind} behind` : null,
            status.upstreamBehind != null ? `${status.upstreamBranch || 'upstream'}: ${status.upstreamBehind} behind` : null
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      ) : null}
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
    queryFn: async () => (await api?.getStage?.()) ?? null,
    queryKey: [...ROOT_KEY, 'stage'],
    refetchInterval: query =>
      (query.state.data as UpdateStageSnapshot | null)?.state === 'preparing' ? 2_000 : false,
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
    onSettled: invalidate
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

  const openNative = () => {
    setActionError(null)

    try {
      const open = api?.openNative ?? api?.open

      if (!open) {
        throw new Error('The native updater is unavailable in this Desktop build.')
      }

      open()
    } catch (error) {
      setActionError(friendlyError(error))
    }
  }

  const status = statusQuery.data ?? null
  const stage = (stageQuery.data ?? null) as UpdateStageSnapshot | null
  const history = (historyQuery.data ?? []) as UpdateHistoryEntry[]
  const commits = stage?.commits ?? status?.commits ?? []
  const queryError = statusQuery.error || stageQuery.error || historyQuery.error
  const primaryLoading = statusQuery.isPending || (target === 'client' && stageQuery.isPending)

  return (
    <div className="h-full min-h-0 overflow-y-auto">
      <main className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 sm:py-7">
        <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
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
          <Button disabled={!api?.openNative && !api?.open} onClick={openNative} size="sm" variant="outline">
            <Codicon name="link-external" size="0.8rem" />
            Native updater
          </Button>
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
          <section aria-labelledby="backend-handoff-heading" className="border-t border-(--ui-stroke-tertiary) pt-5">
            <h2 className="text-sm font-semibold text-(--ui-text-primary)" id="backend-handoff-heading">
              Backend update handoff
            </h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-(--ui-text-tertiary)">
              Backend updates remain core-owned because this connection may be remote. Open the native updater to review and apply its update safely.
            </p>
            <Button className="mt-3" disabled={!api?.openNative && !api?.open} onClick={openNative} size="sm" variant="outline">
              Open native updater
            </Button>
          </section>
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
            <PendingChanges commits={commits} filesChanged={stage?.filesChanged} shortstat={stage?.shortstat} />
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
            Lifecycle controls require a newer Desktop core. The native updater remains available as a compatibility fallback.
          </p>
        ) : null}
      </main>
    </div>
  )
}

const plugin: HermesPlugin = {
  id: 'update-control',
  name: 'Update Control',
  defaultEnabled: false,
  register(ctx) {
    const open = () => ctx.panes.reveal(PANE_ID)

    ctx.registerMany([
      {
        id: PANE_ID,
        area: PANES_AREA,
        title: 'Update Control',
        data: {
          closeBehavior: 'dismiss',
          minWidth: '22vw',
          placement: 'main',
          tabLead: () => <Codicon name="cloud-download" size="0.8rem" />
        },
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
