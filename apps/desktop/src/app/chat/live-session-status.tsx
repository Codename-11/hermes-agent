import { GlyphSpinner } from '@/components/ui/glyph-spinner'
import { cn } from '@/lib/utils'

interface LiveSessionStatusProps {
  awaitingResponse: boolean
  busy: boolean
  className?: string
  runningLabel: string
  waitingLabel: string
}

/** Persistent, session-scoped turn status anchored above the composer. */
export function LiveSessionStatus({
  awaitingResponse,
  busy,
  className,
  runningLabel,
  waitingLabel
}: LiveSessionStatusProps) {
  if (!busy) {
    return null
  }

  const label = awaitingResponse ? waitingLabel : runningLabel

  return (
    <div
      aria-live="polite"
      className={cn(
        'flex min-h-7 shrink-0 items-center gap-2 border-t border-(--ui-border-subtle) bg-(--ui-chat-surface-background) px-4 text-[0.6875rem] font-medium text-(--ui-text-tertiary)',
        className
      )}
      data-slot="live-session-status"
    >
      {awaitingResponse ? (
        <span aria-hidden="true" className="size-1.5 rounded-full bg-amber-500" />
      ) : (
        <GlyphSpinner ariaLabel={label} className="text-(--ui-accent)" spinner="braille" />
      )}
      <span>{label}</span>
    </div>
  )
}
