import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { LiveSessionStatus } from './live-session-status'

afterEach(cleanup)

describe('LiveSessionStatus', () => {
  it('stays visible while the selected session is running', () => {
    render(
      <LiveSessionStatus awaitingInput={false} busy runningLabel="Working…" waitingLabel="Waiting for your input" />
    )

    expect(screen.getByText('Working…')).toBeTruthy()
  })

  it('shows blocking-input state instead of working', () => {
    render(<LiveSessionStatus awaitingInput busy runningLabel="Working…" waitingLabel="Waiting for your input" />)

    expect(screen.getByText('Waiting for your input')).toBeTruthy()
    expect(screen.queryByText('Working…')).toBeNull()
  })

  it('clears when the turn is no longer busy', () => {
    render(
      <LiveSessionStatus
        awaitingInput={false}
        busy={false}
        runningLabel="Working…"
        waitingLabel="Waiting for your input"
      />
    )

    expect(screen.queryByRole('status')).toBeNull()
  })

  it('keeps the label accessible while compacting it visually', () => {
    render(
      <LiveSessionStatus
        awaitingInput={false}
        busy
        compact
        runningLabel="Working…"
        waitingLabel="Waiting for your input"
      />
    )

    const status = screen.getByText('Working…').closest('[data-slot="live-session-status"]')

    expect(status?.getAttribute('data-compact')).toBe('true')
    expect(screen.getByText('Working…').classList.contains('sr-only')).toBe(true)
  })
})
