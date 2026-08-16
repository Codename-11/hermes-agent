import { GlyphSpinner } from '@/components/ui/glyph-spinner'
import { cn } from '@/lib/utils'

interface LiveSessionStatusProps {
  awaitingInput: boolean
  busy: boolean
  className?: string
  compact?: boolean
  runningLabel: string
  waitingLabel: string
}

/** Persistent, session-scoped turn status rendered in the composer controls. */
export function LiveSessionStatus({
  awaitingInput,
  busy,
  className,
  compact = false,
  runningLabel,
  waitingLabel
}: LiveSessionStatusProps) {
  if (!busy) {
    return null
  }

  const label = awaitingInput ? waitingLabel : runningLabel

  return (
    <div
      aria-live="polite"
      className={cn(
        'pointer-events-none flex min-w-0 items-center justify-center gap-2 self-center text-[0.6875rem] font-medium text-(--ui-text-tertiary)',
        className
      )}
      data-compact={compact ? 'true' : undefined}
      data-slot="live-session-status"
    >
      {awaitingInput ? (
        <span aria-hidden="true" className="size-1.5 rounded-full bg-amber-500" />
      ) : (
        <GlyphSpinner ariaLabel={label} className="text-(--ui-accent)" spinner="braille" />
      )}
      <span className={compact ? 'sr-only' : 'truncate'}>{label}</span>
    </div>
  )
}
