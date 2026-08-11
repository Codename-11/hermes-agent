import { Badge, Codicon, fmtDateTime, StatusDot } from '@hermes/plugin-sdk'

import { formatHistoryEntry, type UpdateHistoryEntry } from './model'
import { PendingChanges } from './pending-changes'

function HistoryRow({ entry }: { entry: UpdateHistoryEntry }) {
  const view = formatHistoryEntry(entry)
  const finished = entry.finishedAt ? fmtDateTime.format(entry.finishedAt) : null

  return (
    <details className="group border-t border-(--ui-stroke-tertiary) py-3 first:border-t-0">
      <summary className="grid cursor-pointer list-none grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-3">
        <StatusDot tone={view.tone} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs font-medium text-(--ui-text-primary)">{view.range}</span>
            {entry.branch ? <Badge>{entry.branch}</Badge> : null}
          </div>
          <p className="mt-1 truncate text-xs text-(--ui-text-tertiary)">{view.summary}</p>
          <p className="mt-1 text-[0.6875rem] text-(--ui-text-quaternary)">
            {[finished, view.duration, entry.phase].filter(Boolean).join(' · ')}
          </p>
        </div>
        <span className="flex items-center gap-2 text-xs text-(--ui-text-secondary)">
          {view.resultLabel}
          <Codicon className="transition-transform group-open:rotate-90" name="chevron-right" size="0.75rem" />
        </span>
      </summary>

      <div className="ml-5 mt-3 pl-3">
        {entry.briefPath || entry.logPath ? (
          <dl className="mb-4 space-y-1 text-[0.6875rem] text-(--ui-text-tertiary)">
            {entry.briefPath ? (
              <div className="flex min-w-0 gap-2">
                <dt className="shrink-0 font-medium text-(--ui-text-secondary)">Brief</dt>
                <dd className="truncate font-mono">{entry.briefPath}</dd>
              </div>
            ) : null}
            {entry.logPath ? (
              <div className="flex min-w-0 gap-2">
                <dt className="shrink-0 font-medium text-(--ui-text-secondary)">Log</dt>
                <dd className="truncate font-mono">{entry.logPath}</dd>
              </div>
            ) : null}
          </dl>
        ) : null}
        <PendingChanges commits={entry.commits ?? []} filesChanged={entry.filesChanged} shortstat={entry.shortstat} />
      </div>
    </details>
  )
}

export function UpdateHistory({ entries }: { entries: readonly UpdateHistoryEntry[] }) {
  return (
    <section aria-labelledby="history-heading" className="border-t border-(--ui-stroke-tertiary) pt-5">
      <div>
        <h2 className="text-sm font-semibold text-(--ui-text-primary)" id="history-heading">
          History
        </h2>
        <p className="mt-1 text-xs text-(--ui-text-tertiary)">Completed and failed lifecycle attempts from this install.</p>
      </div>

      {entries.length === 0 ? (
        <p className="mt-4 text-xs leading-5 text-(--ui-text-tertiary)">No update history has been recorded yet.</p>
      ) : (
        <div className="mt-3">
          {entries.map(entry => (
            <HistoryRow entry={entry} key={entry.id} />
          ))}
        </div>
      )}
    </section>
  )
}
