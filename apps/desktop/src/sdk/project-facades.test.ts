import { afterEach, describe, expect, it, vi } from 'vitest'

import { registry } from '@/contrib/registry'
import { desktopGit } from '@/lib/desktop-git'

import {
  createPluginWorktrees,
  DEFAULT_WORKSPACE_RESOLVER_AREA,
  moveSessionWorkspace,
  resolveDefaultWorkspace,
  type DefaultWorkspaceResolverContribution
} from './project-facades'

vi.mock('@/lib/desktop-git', () => ({ desktopGit: vi.fn() }))

const disposers: Array<() => void> = []

afterEach(() => {
  vi.clearAllMocks()
  disposers.splice(0).forEach(dispose => dispose())
})

describe('curated worktree facade', () => {
  it('routes every worktree and branch operation through the remote-aware desktopGit facade', async () => {
    const git = {
      worktreeList: vi.fn(async () => [{ branch: 'main', path: '/repo' }]),
      worktreeAdd: vi.fn(async () => ({ branch: 'feature', path: '/repo-feature', repoRoot: '/repo' })),
      worktreeRemove: vi.fn(async () => ({ removed: '/repo-feature' })),
      branchList: vi.fn(async () => [{ name: 'main' }]),
      baseBranchList: vi.fn(async () => [{ name: 'origin/main' }]),
      branchSwitch: vi.fn(async () => ({ branch: 'main' }))
    }

    vi.mocked(desktopGit).mockReturnValue(git as never)
    const worktrees = createPluginWorktrees()

    await expect(worktrees.list('/repo')).resolves.toEqual([{ branch: 'main', path: '/repo' }])
    await expect(worktrees.add('/repo', { branch: 'feature', base: 'main' })).resolves.toMatchObject({
      branch: 'feature',
      path: '/repo-feature'
    })
    await expect(worktrees.remove('/repo', '/repo-feature', { force: true })).resolves.toEqual({
      removed: '/repo-feature'
    })
    await expect(worktrees.branchList('/repo')).resolves.toEqual([{ name: 'main' }])
    await expect(worktrees.baseBranchList('/repo')).resolves.toEqual([{ name: 'origin/main' }])
    await expect(worktrees.switch('/repo', 'main')).resolves.toEqual({ branch: 'main' })

    expect(git.worktreeList).toHaveBeenCalledWith('/repo')
    expect(git.worktreeAdd).toHaveBeenCalledWith('/repo', { branch: 'feature', base: 'main' })
    expect(git.worktreeRemove).toHaveBeenCalledWith('/repo', '/repo-feature', { force: true })
    expect(git.branchList).toHaveBeenCalledWith('/repo')
    expect(git.baseBranchList).toHaveBeenCalledWith('/repo')
    expect(git.branchSwitch).toHaveBeenCalledWith('/repo', 'main')
  })

  it('fails clearly when the existing desktop Git capability is unavailable', async () => {
    vi.mocked(desktopGit).mockReturnValue(undefined)

    await expect(createPluginWorktrees().list('/repo')).rejects.toThrow(/Git worktree capability.*unavailable/i)
  })
})

describe('typed session workspace move', () => {
  it('maps a folder move and an unassignment to the existing gateway RPC', async () => {
    const request = vi.fn(async (_method: string, params: Record<string, unknown>) => {
      const unassigned = params.unassigned === true
      const cwd = unassigned ? '/home/user' : String(params.cwd)

      return {
        branch: unassigned ? null : 'main',
        cwd,
        git_repo_root: unassigned ? null : cwd
      }
    })

    await expect(
      moveSessionWorkspace(request, { cwd: '/repo', profile: 'worker', sessionId: 'stored-1' })
    ).resolves.toMatchObject({ cwd: '/repo' })
    expect(request).toHaveBeenNthCalledWith(1, 'session.workspace.move', {
      cwd: '/repo',
      profile: 'worker',
      session_key: 'stored-1'
    })

    await moveSessionWorkspace(request, { cwd: null, sessionId: 'stored-1' })
    expect(request).toHaveBeenNthCalledWith(2, 'session.workspace.move', {
      session_key: 'stored-1',
      unassigned: true
    })
  })

  it('rejects blank identifiers before issuing an RPC', async () => {
    const request = vi.fn()

    await expect(moveSessionWorkspace(request, { cwd: '/repo', sessionId: ' ' })).rejects.toThrow(
      /session id required/i
    )
    expect(request).not.toHaveBeenCalled()
  })
})

describe('default workspace resolver contributions', () => {
  const register = (id: string, resolve: DefaultWorkspaceResolverContribution['resolve'], order?: number) => {
    disposers.push(
      registry.register({
        area: DEFAULT_WORKSPACE_RESOLVER_AREA,
        data: { resolve } satisfies DefaultWorkspaceResolverContribution,
        id,
        order,
        source: `plugin:${id}`
      })
    )
  }

  it('uses the first valid contributed workspace in registry order', () => {
    register('later', () => '/repo/later', 20)
    register('first', () => ' /repo/first ', 10)

    expect(resolveDefaultWorkspace(() => '/core/default')).toBe('/repo/first')
  })

  it('contains broken resolvers and safely falls back to the core default', () => {
    register('throws', () => {
      throw new Error('plugin failed')
    })
    register('blank', () => '   ')

    expect(resolveDefaultWorkspace(() => '/core/default')).toBe('/core/default')
    expect(resolveDefaultWorkspace(() => '')).toBeNull()
  })
})
