import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type * as React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/hermes'
import { $activeGatewayProfile } from '@/store/profile'

import { SidebarSessionsSection, VIRTUALIZE_THRESHOLD } from './sessions-section'
import type { VirtualSessionListProps } from './virtual-session-list'

afterEach(() => {
  cleanup()
  $activeGatewayProfile.set('default')
})

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      sidebar: {
        dateDivider: {
          earlierThisMonth: 'Earlier this month',
          lastMonth: 'Last month',
          lastWeek: 'Last week',
          older: 'Older',
          today: 'Today',
          yesterday: 'Yesterday'
        },
        newSessionIn: (label: string) => `New session in ${label}`,
        showProjects: 'Show projects',
        projects: {
          hideOverview: 'Hide projects',
          sectionLabel: 'Projects',
          enter: (label: string) => `Open ${label}`,
          reorder: (label: string) => `Reorder ${label}`,
          toggle: (label: string, open: boolean) => `${open ? 'Show' : 'Hide'} ${label} sessions`,
          viewAllSessions: (count: number) => `View all ${count} sessions`
        }
      }
    }
  })
}))

const mockVirtualListPropsHistory: VirtualSessionListProps[] = []

vi.mock('./virtual-session-list', () => ({
  VirtualSessionList: (props: VirtualSessionListProps) => {
    mockVirtualListPropsHistory.push(props)

    return <div data-testid="virtual-session-list">Virtual List ({props.rows.length} rows)</div>
  }
}))

vi.mock('./session-row', () => ({
  SidebarSessionRow: ({
    isSelected,
    onResume,
    session
  }: {
    isSelected: boolean
    onResume: () => void
    session: SessionInfo
  }) => (
    <button
      data-profile={session.profile}
      data-selected={isSelected}
      data-testid={`session-row-${session.id}`}
      onClick={onResume}
      type="button"
    >
      {session.id}
    </button>
  )
}))

function makeSession(id: string, startedAt = 1000): SessionInfo {
  return {
    handoff_platform: null,
    handoff_state: null,
    id,
    last_active: startedAt,
    profile: 'default',
    started_at: startedAt
  } as unknown as SessionInfo
}

function generateSessions(count: number): SessionInfo[] {
  return Array.from({ length: count }, (_, i) => makeSession(`session-${i + 1}`, 10000 - i * 100))
}

const noop = () => {}

describe('SidebarSessionsSection memoization & virtualizer stability', () => {
  it('preserves the owning profile when opening a session from the unified list', () => {
    const onResumeSession = vi.fn()
    const session = { ...makeSession('shared-id'), profile: 'meta' }

    render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={onResumeSession}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open
        pinned={false}
        sessions={[session]}
      />
    )

    fireEvent.click(screen.getByTestId('session-row-shared-id'))

    expect(onResumeSession).toHaveBeenCalledWith('shared-id', 'meta')
  })

  it('selects only the active profile copy when two profiles share a session id', () => {
    $activeGatewayProfile.set('meta')

    render(
      <SidebarSessionsSection
        activeSessionId="shared-id"
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open
        pinned={false}
        sessions={[
          { ...makeSession('shared-id'), profile: 'default' },
          { ...makeSession('shared-id'), profile: 'meta' }
        ]}
      />
    )

    const rows = screen.getAllByTestId('session-row-shared-id')
    const defaultRow = rows.find(row => row.dataset.profile === 'default')
    const metaRow = rows.find(row => row.dataset.profile === 'meta')

    expect(defaultRow?.dataset.selected).toBe('false')
    expect(metaRow?.dataset.selected).toBe('true')
  })

  it('memoizes flatRows and passes the exact same rows array reference across parent re-renders', () => {
    mockVirtualListPropsHistory.length = 0

    const sessions = generateSessions(VIRTUALIZE_THRESHOLD + 5)

    const { rerender } = render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open={true}
        pinned={false}
        sessions={sessions}
      />
    )

    expect(mockVirtualListPropsHistory.length).toBe(1)
    const initialRowsRef = mockVirtualListPropsHistory[0].rows
    expect(initialRowsRef.length).toBeGreaterThan(VIRTUALIZE_THRESHOLD)

    // Re-render parent with the exact same sessions array and props
    rerender(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open={true}
        pinned={false}
        sessions={sessions}
      />
    )

    expect(mockVirtualListPropsHistory.length).toBe(2)
    const nextRowsRef = mockVirtualListPropsHistory[1].rows

    // Confirm that the flatRows array reference remains strictly identical across renders (useMemo proof)
    expect(nextRowsRef).toBe(initialRowsRef)
  })

  it('re-computes flatRows reference when grouping or sessions change', () => {
    mockVirtualListPropsHistory.length = 0

    const initialSessions = generateSessions(VIRTUALIZE_THRESHOLD + 2)

    const { rerender } = render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        grouping="none"
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open={true}
        pinned={false}
        sessions={initialSessions}
      />
    )

    const firstRowsRef = mockVirtualListPropsHistory[0].rows

    // Switch on date dividers
    rerender(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        grouping="date"
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open={true}
        pinned={false}
        sessions={initialSessions}
      />
    )

    const secondRowsRef = mockVirtualListPropsHistory[1].rows
    expect(secondRowsRef).not.toBe(firstRowsRef)

    // Change sessions array identity
    const updatedSessions = generateSessions(VIRTUALIZE_THRESHOLD + 4)
    rerender(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        grouping="date"
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open={true}
        pinned={false}
        sessions={updatedSessions}
      />
    )

    const thirdRowsRef = mockVirtualListPropsHistory[2].rows
    expect(thirdRowsRef).not.toBe(secondRowsRef)
  })
})

describe('SidebarSessionsSection hybrid project overview', () => {
  const homeProject = {
    id: '__no_project__',
    isNoProject: true,
    label: 'Home',
    path: null,
    repos: [],
    sessionCount: 0
  }

  it('renders Projects first and a separate flat Recent Sessions lane beneath', () => {
    const { container } = render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        grouping="date"
        label="Projects"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open
        pinned={false}
        projectOverview={[homeProject] as never}
        projectOverviewRecentsLabel="Recent Sessions"
        sessions={[makeSession('recent-1')]}
      />
    )

    const labels = screen.getAllByText(/Projects|Home|Recent Sessions|recent-1/).map(node => node.textContent)
    expect(labels).toContain('Projects')
    expect(labels).toContain('Home')
    expect(labels).toContain('Recent Sessions')
    expect(container.textContent?.indexOf('Projects')).toBeLessThan(
      container.textContent?.indexOf('Recent Sessions') ?? -1
    )
    expect(screen.getByTestId('session-row-recent-1')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Show Home sessions' })).toBeNull()
  })

  it('keeps sessions under Recent Sessions when the project tree is empty', () => {
    render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>No projects</div>}
        grouping="date"
        label="Projects"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open
        pinned={false}
        projectOverview={[]}
        projectOverviewRecentsLabel="Recent Sessions"
        sessions={[makeSession('recent-empty-tree')]}
      />
    )

    expect(screen.getByText('Recent Sessions')).toBeTruthy()
    expect(screen.getByTestId('session-row-recent-empty-tree')).toBeTruthy()
  })

  it('collapses only the project overview while keeping Recent Sessions visible', () => {
    const onToggleProjectOverview = vi.fn()

    const { rerender } = render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        grouping="date"
        label="Projects"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleProjectOverview={onToggleProjectOverview}
        onToggleUnread={noop}
        open
        pinned={false}
        projectOverview={[homeProject] as never}
        projectOverviewOpen
        projectOverviewRecentsLabel="Recent Sessions"
        sessions={[makeSession('recent-1')]}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Hide projects' }))
    expect(onToggleProjectOverview).toHaveBeenCalledOnce()

    rerender(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        grouping="date"
        label="Projects"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleProjectOverview={onToggleProjectOverview}
        onToggleUnread={noop}
        open
        pinned={false}
        projectOverview={[homeProject] as never}
        projectOverviewOpen={false}
        projectOverviewRecentsLabel="Recent Sessions"
        sessions={[makeSession('recent-1')]}
      />
    )

    expect(screen.queryByText('Home')).toBeNull()
    expect(screen.getByText('Recent Sessions')).toBeTruthy()
    expect(screen.getByTestId('session-row-recent-1')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Show projects' })).toBeTruthy()
  })

  it('expands a bounded five-session Home preview and keeps full drill-in separate', () => {
    const onEnterProject = vi.fn()
    const previews = generateSessions(5)

    render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        label="Projects"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onEnterProject={onEnterProject}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open
        pinned={false}
        projectOverview={[{ ...homeProject, previewSessions: previews, sessionCount: 9 }] as never}
        sessions={[]}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Show Home sessions' }))

    for (const preview of previews) {
      expect(screen.getByTestId(`session-row-${preview.id}`)).toBeTruthy()
    }

    expect(screen.getAllByTestId(/^session-row-/)).toHaveLength(5)

    fireEvent.click(screen.getByRole('button', { name: 'View all 9 sessions' }))
    expect(onEnterProject).toHaveBeenCalledWith('__no_project__')
  })

  it('hides global recents while drilled into a project', () => {
    render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Project empty</div>}
        label="Demo"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        onToggleUnread={noop}
        open
        pinned={false}
        projectContent={{ ...homeProject, id: 'p-demo', isNoProject: false, label: 'Demo' } as never}
        projectOverviewRecentsLabel="Recent Sessions"
        sessions={[makeSession('global-recent')]}
      />
    )

    expect(screen.getByText('Project empty')).toBeTruthy()
    expect(screen.queryByText('Recent Sessions')).toBeNull()
    expect(screen.queryByTestId('session-row-global-recent')).toBeNull()
  })
})
