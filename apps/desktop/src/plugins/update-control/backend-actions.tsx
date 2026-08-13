import { Badge, Button, cn, Codicon, LogView, StatusDot } from '@hermes/plugin-sdk'
import { useState } from 'react'

import { type BackendUpdateApplySnapshot, hasUpdate, type UpdateSummary } from './model'

export function BackendUpdateActions({
  apply,
  busy,
  onApply,
  onRefresh,
  status
}: {
  apply: BackendUpdateApplySnapshot | null
  busy: boolean
  onApply: () => void
  onRefresh: () => void
  status: UpdateSummary | null
}) {
  const [outputOpen, setOutputOpen] = useState(false)
  const updating = apply?.applying === true
  const failed = !!apply?.error || apply?.stage === 'error'
  const manual = apply?.stage === 'manual'
  const available = hasUpdate(status)

  const title = updating
    ? 'Updating backend'
    : failed
      ? 'Backend update failed'
      : manual
        ? 'Manual backend update required'
        : available
          ? 'Backend update ready'
          : 'Backend is current'

  const description =
    apply?.message ||
    (available
      ? 'Apply the connected backend update here. Update Control will track progress through restart and reconnection.'
      : 'No backend update is currently available.')

  const tone = failed ? 'bad' : updating || available || manual ? 'warn' : 'good'
  const progress = apply?.percent == null ? null : Math.max(0, Math.min(100, apply.percent))

  return (
    <section aria-labelledby="backend-update-heading" className="border-t border-(--ui-stroke-tertiary) pt-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusDot tone={tone} />
            <h2 className="text-sm font-semibold text-(--ui-text-primary)" id="backend-update-heading">
              {title}
            </h2>
            <Badge>{apply?.stage || (available ? 'available' : 'current')}</Badge>
          </div>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-(--ui-text-tertiary)">{description}</p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {available || failed ? (
            <Button disabled={busy || updating} onClick={onApply} size="sm">
              {busy || updating ? <Codicon className="animate-spin" name="loading" size="0.8rem" /> : null}
              {failed ? 'Retry backend update' : updating ? 'Updating…' : 'Update backend'}
            </Button>
          ) : (
            <Button disabled={busy} onClick={onRefresh} size="sm" variant="outline">
              Recheck source
            </Button>
          )}
        </div>
      </div>

      {progress != null ? (
        <div aria-label={`Backend update ${progress}%`} aria-valuemax={100} aria-valuenow={progress} className="mt-4" role="progressbar">
          <div className="h-1 overflow-hidden rounded-full bg-(--ui-bg-quaternary)">
            <div className="h-full rounded-full bg-(--ui-accent) transition-[width]" style={{ width: `${progress}%` }} />
          </div>
          <div className="mt-1.5 flex justify-between text-[0.6875rem] text-(--ui-text-quaternary)">
            <span>{apply?.stage}</span>
            <span>{progress}%</span>
          </div>
        </div>
      ) : null}

      {apply?.command ? (
        <div className={cn('mt-4 border-l-2 border-(--ui-accent) pl-3 text-xs leading-5 text-(--ui-text-tertiary)')}>
          Manual command: <code className="select-all font-mono text-(--ui-text-secondary)">{apply.command}</code>
        </div>
      ) : null}

      {apply?.output?.trim() ? (
        <div className="mt-4 overflow-hidden rounded-md border border-(--ui-stroke-tertiary)">
          <button
            aria-expanded={outputOpen}
            className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs font-medium text-(--ui-text-secondary) transition-colors hover:bg-(--chrome-action-hover)"
            onClick={() => setOutputOpen(value => !value)}
            type="button"
          >
            <span className="flex items-center gap-2">
              <Codicon name="terminal" size="0.8rem" />
              Backend CLI output
            </span>
            <Codicon name={outputOpen ? 'chevron-down' : 'chevron-right'} size="0.75rem" />
          </button>
          {outputOpen ? (
            <LogView className="max-h-72 rounded-none border-x-0 border-b-0" role="log">
              {apply.output}
            </LogView>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
