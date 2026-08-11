import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BackendUpdateActions } from './backend-actions'

afterEach(cleanup)

describe('BackendUpdateActions', () => {
  it('applies an available backend update inside Update Control', () => {
    const onApply = vi.fn()

    render(
      <BackendUpdateActions
        apply={null}
        busy={false}
        onApply={onApply}
        onRefresh={vi.fn()}
        status={{ behind: 2, supported: true, updateAvailable: true }}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Update backend' }))
    expect(onApply).toHaveBeenCalledOnce()
    expect(screen.queryByText(/native updater/i)).toBeNull()
  })

  it('renders in-plugin progress while the backend updater runs', () => {
    render(
      <BackendUpdateActions
        apply={{
          applying: true,
          stage: 'pull',
          message: 'Installing backend dependencies',
          percent: 42,
          error: null,
          command: null
        }}
        busy
        onApply={vi.fn()}
        onRefresh={vi.fn()}
        status={{ behind: 2, supported: true, updateAvailable: true }}
      />
    )

    expect(screen.getByRole('progressbar', { name: 'Backend update 42%' }).getAttribute('aria-valuenow')).toBe('42')
    expect(screen.getByText('Installing backend dependencies')).toBeTruthy()
  })

  it('offers retry and preserves a manual fallback command after failure', () => {
    render(
      <BackendUpdateActions
        apply={{
          applying: false,
          stage: 'error',
          message: 'Backend update failed',
          percent: null,
          error: 'apply-failed',
          command: 'hermes update'
        }}
        busy={false}
        onApply={vi.fn()}
        onRefresh={vi.fn()}
        status={{ behind: 1, supported: true, updateAvailable: true }}
      />
    )

    expect((screen.getByRole('button', { name: 'Retry backend update' }) as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText('hermes update')).toBeTruthy()
  })
})
