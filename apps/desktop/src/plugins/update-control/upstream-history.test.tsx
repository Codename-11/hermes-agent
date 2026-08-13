import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { UpstreamHistory } from './upstream-history'

afterEach(cleanup)

describe('UpstreamHistory', () => {
  const commits = [
    { sha: 'desktop123', summary: 'feat(desktop): add update controls', author: 'A' },
    { sha: 'cli123', summary: 'fix(cli): preserve update output', author: 'B' },
    { sha: 'docs123', summary: 'docs: explain updates', author: 'C' }
  ]

  it('shows clean type and scope labels while preserving newest-first input order', () => {
    render(<UpstreamHistory commits={commits} description="3 upstream commits" />)

    const rows = screen.getAllByRole('row').slice(1).map(row => row.textContent)
    expect(rows[0]).toContain('FeaturesDesktopadd update controls')
    expect(rows[1]).toContain('FixesCLI & backendpreserve update output')
    expect(rows[2]).toContain('DocsSkills & docsexplain updates')
  })

  it('filters history to Desktop work at a glance', () => {
    render(<UpstreamHistory commits={commits} description="3 upstream commits" />)

    fireEvent.click(screen.getByRole('button', { name: 'Desktop' }))

    expect(screen.getByText('add update controls')).toBeTruthy()
    expect(screen.queryByText('preserve update output')).toBeNull()
    expect(screen.queryByText('explain updates')).toBeNull()
  })

  it('shows 25 commits per page and pages through the remaining history', () => {
    const many = Array.from({ length: 27 }, (_, index) => ({
      author: `Author ${index + 1}`,
      sha: `sha-${String(index + 1).padStart(2, '0')}`,
      summary: `fix(desktop): commit ${index + 1}`
    }))

    render(<UpstreamHistory commits={many} description="27 upstream commits" />)

    expect(screen.getAllByRole('row')).toHaveLength(26)
    expect(screen.getByText('commit 1')).toBeTruthy()
    expect(screen.queryByText('commit 26')).toBeNull()
    expect(screen.getByText('1–25 of 27')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))

    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText('commit 26')).toBeTruthy()
    expect(screen.getByText('commit 27')).toBeTruthy()
    expect(screen.getByText('26–27 of 27')).toBeTruthy()
  })

  it('returns to page one when the scope or commit payload changes', () => {
    const many = Array.from({ length: 27 }, (_, index) => ({
      author: 'A',
      sha: `desktop-${index}`,
      summary: `fix(desktop): desktop commit ${index + 1}`
    }))

    const view = render(<UpstreamHistory commits={many} description="27 upstream commits" />)

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    fireEvent.click(screen.getByRole('button', { name: 'Desktop' }))
    expect(screen.getByText('1–25 of 27')).toBeTruthy()

    view.rerender(<UpstreamHistory commits={many.slice(0, 3)} description="3 upstream commits" />)
    expect(screen.getByText('1–3 of 3')).toBeTruthy()
    expect(screen.getByText('desktop commit 1')).toBeTruthy()
  })
})
