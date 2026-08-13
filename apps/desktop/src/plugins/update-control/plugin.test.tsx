import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import plugin, { CliOutput } from './plugin'

describe('Update Control plugin registration', () => {
  it('ships enabled and contributes a reopenable main tab beside existing sessions', () => {
    const registerMany = vi.fn()
    const reveal = vi.fn()

    plugin.register({ panes: { reveal }, registerMany } as never)

    expect(plugin).toMatchObject({
      defaultEnabled: true,
      id: 'update-control',
      name: 'Update Control'
    })
    expect(registerMany).toHaveBeenCalledTimes(1)

    const contributions = registerMany.mock.calls[0]?.[0] as Array<{
      area: string
      data?: { label?: string; onSelect?: () => void; path?: string; run?: () => void }
      id: string
      render?: unknown
      title?: string
    }>

    expect(contributions.map(contribution => contribution.id)).toEqual(['panel', 'nav', 'status', 'open'])
    expect(contributions.find(contribution => contribution.id === 'panel')).toMatchObject({
      area: 'panes',
      data: { closeBehavior: 'dismiss', placement: 'main' },
      title: 'Update Control'
    })
    expect(contributions.find(contribution => contribution.id === 'nav')).toMatchObject({
      area: 'sidebar.nav',
      data: { label: 'Update Control' }
    })
    expect(contributions.find(contribution => contribution.id === 'status')?.render).toBeTypeOf('function')
    expect(contributions.find(contribution => contribution.id === 'open')?.data?.label).toBe('Update Control: Open')

    contributions.find(contribution => contribution.id === 'nav')?.data?.onSelect?.()
    contributions.find(contribution => contribution.id === 'open')?.data?.run?.()
    expect(reveal).toHaveBeenNthCalledWith(1, 'panel')
    expect(reveal).toHaveBeenNthCalledWith(2, 'panel')
  })
})

describe('Update Control CLI output', () => {
  it('opens immediately during sync and shows a placeholder before the first CLI line', () => {
    render(<CliOutput defaultOpen label="Reconcile CLI output" />)

    expect(screen.getByRole('button', { name: /Reconcile CLI output/ }).getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('log').textContent).toContain('Waiting for CLI output…')
  })

  it('renders the current live transcript in the open panel', () => {
    render(<CliOutput defaultOpen label="Reconcile CLI output" output={'→ Fetching upstream…\n✓ Worktree ready'} />)

    expect(screen.getByRole('log').textContent).toContain('→ Fetching upstream…')
    expect(screen.getByRole('log').textContent).toContain('✓ Worktree ready')
  })
})
