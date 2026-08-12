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

    const subjects = screen.getAllByRole('listitem').map(item => item.textContent)
    expect(subjects[0]).toContain('FeaturesDesktopadd update controls')
    expect(subjects[1]).toContain('FixesCLI & backendpreserve update output')
    expect(subjects[2]).toContain('DocsSkills & docsexplain updates')
  })

  it('filters history to Desktop work at a glance', () => {
    render(<UpstreamHistory commits={commits} description="3 upstream commits" />)

    fireEvent.click(screen.getByRole('button', { name: 'Desktop' }))

    expect(screen.getByText('add update controls')).toBeTruthy()
    expect(screen.queryByText('preserve update output')).toBeNull()
    expect(screen.queryByText('explain updates')).toBeNull()
  })
})
