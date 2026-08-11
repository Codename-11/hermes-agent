import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ProjectOverviewRow } from './overview-row'
import type { SidebarProjectTree } from './workspace-groups'

afterEach(cleanup)

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      sidebar: {
        newSessionIn: (label: string) => `New session in ${label}`,
        projects: {
          enter: (label: string) => `Enter ${label}`,
          reorder: (label: string) => `Reorder ${label}`,
          toggle: (label: string, open: boolean) => `${open ? 'Show' : 'Hide'} ${label} sessions`
        }
      }
    }
  })
}))

// ProjectMenu (the kebab) has its own dedicated test file — stub it here so
// this file only exercises overview-row's own Tip usage (the disclosure
// toggle) plus the WorkspaceAddButton wiring. ProjectContextMenu (the row's
// right-click wrapper) is stubbed as a pass-through so the row still renders.
vi.mock('./project-menu', () => ({
  ProjectContextMenu: ({ children }: { children: ReactNode }) => children,
  ProjectMenu: () => null
}))

vi.mock('./project-worktree-actions', () => ({
  ProjectWorktreeMenu: ({ onNewSession, projectLabel, projectPath }: any) => (
    <span data-slot="tooltip-trigger">
      <button aria-label={`New session in ${projectLabel}`} onClick={() => onNewSession(projectPath)} type="button" />
    </span>
  )
}))

const project = {
  id: 'p1',
  label: 'Test D',
  path: '/repo/test-d',
  repos: [],
  sessionCount: 2
} as unknown as SidebarProjectTree

const tipTrigger = (el: HTMLElement) => el.closest('[data-slot="tooltip-trigger"]')

describe('ProjectOverviewRow', () => {
  it('wraps the "new session" add button in a Tip with the project-scoped label', () => {
    render(<ProjectOverviewRow onNewSession={vi.fn()} project={project} />)

    const button = screen.getByRole('button', { name: 'New session in Test D' })
    expect(tipTrigger(button)).toBeTruthy()
  })

  it('uses a separate disclosure control from the Project drill-in label', () => {
    const onEnter = vi.fn()
    const onToggleExpanded = vi.fn()

    render(<ProjectOverviewRow onEnter={onEnter} onToggleExpanded={onToggleExpanded} project={project} />)

    fireEvent.click(screen.getByRole('button', { name: 'Show Test D sessions' }))
    expect(onToggleExpanded).toHaveBeenCalledOnce()
    expect(onEnter).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Enter Test D' }))
    expect(onEnter).toHaveBeenCalledWith('p1')
  })

  it('offers the "new session" add button on Home, which starts one with no folder', () => {
    const home = {
      id: '__no_project__',
      isNoProject: true,
      label: 'Home',
      path: null
    } as unknown as SidebarProjectTree

    const onNewSession = vi.fn()

    render(<ProjectOverviewRow onNewSession={onNewSession} project={home} />)
    fireEvent.click(screen.getByRole('button', { name: 'New session in Home' }))

    expect(onNewSession).toHaveBeenCalledWith(null)
  })

  it('marks Home active when the durable project target is clear and exposes its add button on row hover', () => {
    const home = {
      id: '__no_project__',
      isNoProject: true,
      label: 'Home',
      path: null
    } as unknown as SidebarProjectTree

    const { container } = render(<ProjectOverviewRow activeProjectId={null} onNewSession={vi.fn()} project={home} />)

    expect(screen.getByText('Home').className).toContain('text-foreground')
    expect(container.querySelector('[class~="group/workspace"]')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'New session in Home' })).toBeTruthy()
  })
})
