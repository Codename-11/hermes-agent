import {
  Badge,
  Button,
  cn,
  Codicon,
  type DesktopUpdateStatus,
  fmtDateTime,
  type HermesPlugin,
  host,
  PALETTE_AREA,
  type PaletteContribution,
  PANES_AREA,
  STATUSBAR_AREAS,
  StatusDot,
  type StatusTone,
  Tip,
  useQuery
} from '@hermes/plugin-sdk'
import { useState } from 'react'

import { friendlyError, hasUpdate, shortSha } from './model'

const PANE_ID = 'panel'
const SNAPSHOTS_KEY = ['update-control', 'snapshots'] as const

type UpdateTarget = 'backend' | 'client'

interface ReadonlyUpdatesApi {
  getStatus?: (target: UpdateTarget) => DesktopUpdateStatus | null
  open?: () => void
}

interface UpdateSnapshots {
  backend: DesktopUpdateStatus | null
  client: DesktopUpdateStatus | null
}

function updatesApi(): ReadonlyUpdatesApi | undefined {
  return (host as typeof host & { updates?: ReadonlyUpdatesApi }).updates
}

function readSnapshots(): UpdateSnapshots {
  const api = updatesApi()

  return {
    backend: api?.getStatus?.('backend') ?? null,
    client: api?.getStatus?.('client') ?? null
  }
}

function useUpdateSnapshots() {
  return useQuery({
    queryFn: readSnapshots,
    queryKey: SNAPSHOTS_KEY,
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
          'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
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

function SummaryCell({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-md border border-border/60 bg-muted/20 px-3 py-2.5">
      <p className="text-[0.625rem] font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <div className="mt-1 truncate text-sm font-medium text-foreground">{value}</div>
    </div>
  )
}

function UpdateStateBadge({ status }: { status: DesktopUpdateStatus | null }) {
  if (!status) {
    return <Badge className="text-muted-foreground">Pending</Badge>
  }

  if (status.error) {
    return <Badge className="text-destructive">Check failed</Badge>
  }

  if (!status.supported) {
    return <Badge className="text-muted-foreground">Unavailable</Badge>
  }

  if (hasUpdate(status)) {
    return <Badge className="text-amber-600 dark:text-amber-400">Update available</Badge>
  }

  return <Badge className="text-primary">Current</Badge>
}

function RecentCommits({ status }: { status: DesktopUpdateStatus | null }) {
  const commits = (status?.commits ?? []).slice(0, 6)

  if (commits.length === 0) {
    return <p className="text-xs leading-5 text-muted-foreground">No recent commit summary was provided.</p>
  }

  return (
    <ul className="divide-y divide-border/50 rounded-md border border-border/60">
      {commits.map(commit => (
        <li className="flex min-w-0 items-start gap-3 px-3 py-2.5" key={commit.sha}>
          <code className="mt-0.5 shrink-0 text-[0.6875rem] text-muted-foreground">{shortSha(commit.sha)}</code>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-foreground">{commit.summary}</p>
            <p className="mt-0.5 truncate text-[0.6875rem] text-muted-foreground">
              {[commit.author, fmtDateTime.format(commit.at)].filter(Boolean).join(' · ')}
            </p>
          </div>
        </li>
      ))}
    </ul>
  )
}

function TargetCard({
  icon,
  status,
  subtitle,
  title
}: {
  icon: string
  status: DesktopUpdateStatus | null
  subtitle: string
  title: string
}) {
  const branch = status?.currentBranch ?? status?.branch ?? '—'
  const current = status?.currentSha ?? status?.currentVersion
  const message = status?.message ?? status?.backendMessage ?? status?.error

  return (
    <section className="min-w-0 rounded-lg border border-border/70 bg-card/40 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-md bg-muted/60 text-muted-foreground">
            <Codicon name={icon} size="1rem" />
          </span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">{title}</h2>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{subtitle}</p>
          </div>
        </div>
        <UpdateStateBadge status={status} />
      </div>

      {message ? (
        <div
          className={cn(
            'mt-4 rounded-md border px-3 py-2 text-xs leading-5',
            status?.error
              ? 'border-destructive/30 bg-destructive/5 text-destructive'
              : 'border-border/60 bg-muted/20 text-muted-foreground'
          )}
        >
          {message}
        </div>
      ) : null}

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <SummaryCell label="Supported" value={status ? (status.supported ? 'Yes' : 'No') : '—'} />
        <SummaryCell
          label="Status"
          value={
            status ? (hasUpdate(status) ? 'Update available' : status.supported ? 'Current' : 'Unavailable') : 'Pending'
          }
        />
        <SummaryCell label="Behind" value={status?.supported ? (status.behind ?? 0) : '—'} />
        <SummaryCell label="Branch" value={branch} />
      </div>

      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <SummaryCell label="Current commit / version" value={shortSha(current)} />
        <SummaryCell label="Target commit" value={shortSha(status?.targetSha)} />
      </div>

      {status?.deployBehind != null || status?.upstreamBehind != null ? (
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <SummaryCell
            label={status.deployBranch ? `Behind ${status.deployBranch}` : 'Deploy branch behind'}
            value={status.deployBehind ?? 0}
          />
          <SummaryCell
            label={status.upstreamBranch ? `Behind ${status.upstreamBranch}` : 'Upstream behind'}
            value={status.upstreamBehind ?? 0}
          />
        </div>
      ) : null}

      {status?.dirty ? (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
          <Codicon name="warning" size="0.8rem" /> Local changes are present. Review them before updating.
        </p>
      ) : null}

      <div className="mt-5">
        <h3 className="mb-2 text-xs font-semibold text-foreground">Recent commits</h3>
        <RecentCommits status={status} />
      </div>
    </section>
  )
}

function UpdateControlPane() {
  const snapshots = useUpdateSnapshots()
  const [openError, setOpenError] = useState<string | null>(null)

  const openUpdater = () => {
    const open = updatesApi()?.open

    if (!open) {
      setOpenError('The native updater is unavailable in this Desktop build.')

      return
    }

    setOpenError(null)

    try {
      open()
    } catch (error) {
      setOpenError(friendlyError(error))
    }
  }

  return (
    <div className="h-full min-h-0 overflow-y-auto">
      <main className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6 sm:py-7">
        <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Codicon className="text-muted-foreground" name="cloud-download" size="1.1rem" />
              <h1 className="text-lg font-semibold tracking-tight text-foreground">Update Control</h1>
            </div>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              Compare core-owned update snapshots for this Desktop client and the connected backend. The native updater
              owns checks, confirmation, install, and restart.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={snapshots.isFetching}
              onClick={() => void snapshots.refetch()}
              size="sm"
              variant="outline"
            >
              <Codicon className={cn(snapshots.isFetching && 'animate-spin')} name="refresh" size="0.8rem" />
              {snapshots.isFetching ? 'Refreshing…' : 'Refresh view'}
            </Button>
            <Button disabled={!updatesApi()?.open} onClick={openUpdater} size="sm">
              <Codicon name="link-external" size="0.8rem" />
              Open native updater
            </Button>
          </div>
        </header>

        {openError || snapshots.error ? (
          <div className="mt-4 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            {openError ?? friendlyError(snapshots.error)}
          </div>
        ) : null}

        <div className="mt-5 grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
          <TargetCard
            icon="device-desktop"
            status={snapshots.data?.client ?? null}
            subtitle="The local Hermes Desktop checkout and app on this device."
            title="Local Desktop client"
          />
          <TargetCard
            icon="server"
            status={snapshots.data?.backend ?? null}
            subtitle="The Hermes runtime serving the active connection, whether local or remote."
            title="Connected backend"
          />
        </div>

        <p className="mt-4 text-xs leading-5 text-muted-foreground">
          Update Control is read-only. Opening the updater refreshes the active target and keeps dirty-tree handling,
          deploy reconciliation, update execution, and process handoff in Hermes core.
        </p>
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
          keywords: ['update', 'version', 'client', 'backend', 'branch'],
          run: open
        } satisfies PaletteContribution
      }
    ])
  }
}

export default plugin
