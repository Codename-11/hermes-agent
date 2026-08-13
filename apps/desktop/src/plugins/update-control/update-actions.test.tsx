import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { UpdateActions } from './update-actions'

const handlers = () => ({
  onCancel: vi.fn(),
  onDiscard: vi.fn(),
  onDiscardAndRefresh: vi.fn(),
  onPrepare: vi.fn(),
  onRefresh: vi.fn(),
  onRestart: vi.fn(),
  onStandardUpdate: vi.fn().mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('UpdateActions preparation activity', () => {
  it('confirms before handing the Desktop to the standard updater', async () => {
    const actions = handlers()

    render(
      <UpdateActions
        {...actions}
        busy={false}
        stage={{ state: 'available' }}
        status={{ behind: 3, supported: true, updateAvailable: true }}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Run standard update' }))

    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText(/Desktop will close/i)).toBeTruthy()
    expect(actions.onStandardUpdate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Close Desktop and update' }))
    await vi.waitFor(() => expect(actions.onStandardUpdate).toHaveBeenCalledOnce())
  })

  it('keeps standard update unavailable during active preparation', () => {
    render(
      <UpdateActions
        {...handlers()}
        busy={false}
        stage={{ state: 'preparing', ownerActive: true }}
        status={{ behind: 3, supported: true, updateAvailable: true }}
      />
    )

    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Run standard update' }).disabled).toBe(true)
  })

  it('keeps Desktop open and surfaces a standard updater launch failure', async () => {
    const actions = handlers()

    actions.onStandardUpdate.mockRejectedValue(new Error('Updater did not launch.'))
    render(
      <UpdateActions
        {...actions}
        busy={false}
        stage={{ state: 'available' }}
        status={{ behind: 3, supported: true, updateAvailable: true }}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Run standard update' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close Desktop and update' }))

    expect(await screen.findByText('Updater did not launch.')).toBeTruthy()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('shows verified heartbeat, spinner, progress, and cancel for a live preparation owner', () => {
    vi.spyOn(Date, 'now').mockReturnValue(12_000)
    const actions = handlers()

    render(
      <UpdateActions
        {...actions}
        busy={false}
        stage={{
          state: 'preparing',
          phase: 'building',
          percent: 55,
          checkedAt: 10_000,
          ownerActive: true,
          cancellable: true,
          message: 'Packaging Desktop'
        }}
        status={{ behind: 2, supported: true, updateAvailable: true }}
      />
    )

    expect(screen.getByLabelText('Preparation active')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('Worker verified · status checked 2s ago')
    expect(screen.getByRole('progressbar', { name: 'Preparation 55%' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel preparation' }))
    expect(actions.onCancel).toHaveBeenCalledOnce()
  })

  it('does not offer cancellation when a preparing snapshot lacks verified live ownership', () => {
    vi.spyOn(Date, 'now').mockReturnValue(12_000)

    render(
      <UpdateActions
        {...handlers()}
        busy={false}
        stage={{
          state: 'preparing',
          phase: 'building',
          percent: 55,
          checkedAt: 10_000,
          ownerActive: false,
          cancellable: false
        }}
        status={{ behind: 2, supported: true, updateAvailable: true }}
      />
    )

    expect(screen.getByRole('status').textContent).toBe('Worker not verified')
    expect(screen.queryByRole('button', { name: 'Cancel preparation' })).toBeNull()
    expect(screen.queryByLabelText('Preparation active')).toBeNull()
  })

  it('never offers preparation cancellation once the stage is ready to apply', () => {
    const actions = handlers()

    render(
      <UpdateActions
        {...actions}
        busy={false}
        stage={{ state: 'ready', phase: 'ready', percent: 100, ownerActive: false, cancellable: false }}
        status={{ behind: 2, supported: true, updateAvailable: true }}
      />
    )

    expect(screen.queryByRole('button', { name: 'Cancel preparation' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Discard & check latest' }))
    expect(actions.onDiscardAndRefresh).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Restart and finish' })).toBeTruthy()
  })
})
