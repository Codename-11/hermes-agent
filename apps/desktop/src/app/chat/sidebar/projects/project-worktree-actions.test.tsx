import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const isGitRepoPath = vi.fn()
const openWorktreeDialog = vi.fn()

vi.mock('@/store/coding-status', () => ({ isGitRepoPath, openWorktreeDialog }))

const { ProjectLifecycleActions } = await import('./project-worktree-actions')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ProjectLifecycleActions', () => {
  it('exposes separate project and isolated-worktree session actions', async () => {
    isGitRepoPath.mockResolvedValue(true)
    const onNewSession = vi.fn()
    const windowsRepoPath = String.raw`C:\src\axiom`

    render(
      <ProjectLifecycleActions
        onNewSession={onNewSession}
        projectLabel="Axiom"
        projectPath={windowsRepoPath}
        repoPath={windowsRepoPath}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'New session in project' }))
    expect(onNewSession).toHaveBeenCalledWith(windowsRepoPath)

    const worktree = screen.getByRole('button', { name: 'New worktree session…' })
    await waitFor(() => expect(worktree.hasAttribute('disabled')).toBe(false))
    fireEvent.click(worktree)
    expect(openWorktreeDialog).toHaveBeenCalledWith({ repoPath: windowsRepoPath })
  })

  it('keeps the worktree action disabled for a non-Git project', async () => {
    isGitRepoPath.mockResolvedValue(false)

    render(
      <ProjectLifecycleActions
        onNewSession={vi.fn()}
        projectLabel="Notes"
        projectPath="/notes"
        repoPath="/notes"
      />
    )

    const worktree = screen.getByRole('button', { name: 'New worktree session…' })
    await waitFor(() => expect(isGitRepoPath).toHaveBeenCalledWith('/notes'))
    expect(worktree.hasAttribute('disabled')).toBe(true)
  })
})
