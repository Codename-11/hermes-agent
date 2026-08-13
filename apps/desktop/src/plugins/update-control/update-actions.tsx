import { Badge, Button, cn, Codicon, ConfirmDialog, StatusDot } from '@hermes/plugin-sdk'
import { useState } from 'react'

import { derivePreparationView, shortSha, type UpdateStageSnapshot, type UpdateSummary } from './model'

export function UpdateActions({
  busy,
  onCancel,
  onDiscard,
  onDiscardAndRefresh,
  onPrepare,
  onRefresh,
  onRestart,
  onStandardUpdate,
  stage,
  status
}: {
  busy: boolean
  onCancel: () => void
  onDiscard: () => void
  onDiscardAndRefresh: () => void
  onPrepare: () => void
  onRefresh: () => void
  onRestart: () => void
  onStandardUpdate: () => Promise<void>
  stage: UpdateStageSnapshot | null
  status: UpdateSummary | null
}) {
  const [standardUpdateOpen, setStandardUpdateOpen] = useState(false)
  const view = derivePreparationView(status, stage)
  const progress = stage?.state === 'preparing' && stage.percent != null ? Math.max(0, Math.min(100, stage.percent)) : null
  const activelyPreparing = stage?.state === 'preparing' && stage.ownerActive === true
  const canCancel = activelyPreparing && stage.cancellable === true
  const checkedAgo = stage?.checkedAt == null ? null : Math.max(0, Math.floor((Date.now() - stage.checkedAt) / 1_000))

  const runPrimary =
    view.action === 'prepare' ? onPrepare : view.action === 'restartAndApply' ? onRestart : view.action === 'refresh' ? onRefresh : null

  const actionLabel =
    view.action === 'prepare'
      ? stage?.state === 'failed' || stage?.state === 'invalid'
        ? 'Prepare again'
        : 'Prepare update'
      : view.action === 'restartAndApply'
        ? 'Restart and finish'
        : view.action === 'refresh'
          ? 'Check again'
          : null

  return (
    <section aria-labelledby="preparation-heading" className="border-t border-(--ui-stroke-tertiary) pt-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {activelyPreparing ? (
              <Codicon aria-label="Preparation active" className="animate-spin text-(--ui-accent)" name="loading" size="0.8rem" />
            ) : (
              <StatusDot tone={view.tone} />
            )}
            <h2 className="text-sm font-semibold text-(--ui-text-primary)" id="preparation-heading">
              {view.title}
            </h2>
            <Badge>{view.state}</Badge>
          </div>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-(--ui-text-tertiary)">{view.description}</p>
          {stage?.state === 'preparing' && checkedAgo != null ? (
            <p aria-live="polite" className="mt-1 text-[0.6875rem] text-(--ui-text-quaternary)" role="status">
              {stage.ownerActive ? `Worker verified · status checked ${checkedAgo < 2 ? 'just now' : `${checkedAgo}s ago`}` : 'Worker not verified'}
            </p>
          ) : null}
          {stage?.targetSha ? (
            <p className="mt-2 font-mono text-[0.6875rem] text-(--ui-text-quaternary)">
              {shortSha(stage.currentSha || status?.currentSha)} → {shortSha(stage.targetSha)}
              {stage.branch ? ` · ${stage.branch}` : ''}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button
            disabled={busy || stage?.state === 'preparing'}
            onClick={() => setStandardUpdateOpen(true)}
            size="sm"
            variant="outline"
          >
            Run standard update
          </Button>
          {canCancel ? (
            <Button disabled={busy} onClick={onCancel} size="sm" variant="outline">
              {busy ? <Codicon className="animate-spin" name="loading" size="0.8rem" /> : null}
              Cancel preparation
            </Button>
          ) : null}
          {stage?.state === 'ready' ? (
            <Button disabled={busy} onClick={onDiscardAndRefresh} size="sm" variant="outline">
              Discard &amp; check latest
            </Button>
          ) : view.canDiscard ? (
            <Button disabled={busy} onClick={onDiscard} size="sm" variant="outline">
              Discard
            </Button>
          ) : null}
          {runPrimary && actionLabel ? (
            <Button disabled={busy || stage?.state === 'preparing'} onClick={runPrimary} size="sm">
              {busy ? <Codicon className="animate-spin" name="loading" size="0.8rem" /> : null}
              {actionLabel}
            </Button>
          ) : null}
        </div>
      </div>

      {progress != null ? (
        <div aria-label={`Preparation ${progress}%`} aria-valuemax={100} aria-valuenow={progress} className="mt-4" role="progressbar">
          <div className="h-1 overflow-hidden rounded-full bg-(--ui-bg-quaternary)">
            <div className="h-full rounded-full bg-(--ui-accent) transition-[width]" style={{ width: `${progress}%` }} />
          </div>
          <div className="mt-1.5 flex justify-between text-[0.6875rem] text-(--ui-text-quaternary)">
            <span>{stage?.phase || 'Preparing'}</span>
            <span>{progress}%</span>
          </div>
        </div>
      ) : null}

      {view.diagnostic ? (
        <div
          className={cn(
            'mt-4 flex items-start gap-2 border-l-2 pl-3 text-xs leading-5',
            view.tone === 'bad'
              ? 'border-destructive text-(--ui-text-secondary)'
              : 'border-(--ui-accent) text-(--ui-text-tertiary)'
          )}
        >
          <Codicon className="mt-0.5 shrink-0" name="warning" size="0.8rem" />
          <span>{view.diagnostic}</span>
        </div>
      ) : null}
      <ConfirmDialog
        busyLabel="Starting updater…"
        confirmLabel="Close Desktop and update"
        description="Desktop will close and run the normal Hermes update flow. This skips preparation; the updater will validate the installation, apply the configured deploy branch, and reopen Desktop when finished."
        doneLabel="Updater started"
        onClose={() => setStandardUpdateOpen(false)}
        onConfirm={onStandardUpdate}
        open={standardUpdateOpen}
        title="Run standard Hermes update?"
      />
    </section>
  )
}
