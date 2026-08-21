import type { HermesGitBaseBranch, HermesGitBranch, HermesGitWorktree } from '@/global'
import { registry } from '@/contrib/registry'
import { desktopGit } from '@/lib/desktop-git'

export const DEFAULT_WORKSPACE_RESOLVER_AREA = 'workspace.defaultResolvers'

export interface DefaultWorkspaceResolverContribution {
  /** Return a cached/default workspace path, or null/blank to decline. */
  resolve: () => null | string | undefined
}

export interface PluginWorktreeAddOptions {
  base?: string
  branch?: string
  existingBranch?: string
  name?: string
}

export interface PluginWorktrees {
  list: (repoPath: string) => Promise<HermesGitWorktree[]>
  add: (
    repoPath: string,
    options?: PluginWorktreeAddOptions
  ) => Promise<{ branch: string; path: string; repoRoot: string }>
  remove: (repoPath: string, worktreePath: string, options?: { force?: boolean }) => Promise<{ removed: string }>
  branchList: (repoPath: string) => Promise<HermesGitBranch[]>
  baseBranchList: (repoPath: string) => Promise<HermesGitBaseBranch[]>
  switch: (repoPath: string, branch: string) => Promise<{ branch: string }>
}

export interface MoveSessionWorkspaceInput {
  /** Null moves the session to the backend's unassigned/Home workspace. */
  cwd: null | string
  profile?: null | string
  /** Durable stored-session id, not the ephemeral runtime id. */
  sessionId: string
}

export interface MovedSessionWorkspace {
  branch?: null | string
  cwd: string
  git_repo_root?: null | string
}

type GatewayRequest = (method: string, params: Record<string, unknown>) => Promise<MovedSessionWorkspace>

const required = (value: string, label: string): string => {
  const trimmed = (value ?? '').trim()

  if (!trimmed) {
    throw new Error(`${label} required`)
  }

  return trimmed
}

const gitCapability = () => {
  const git = desktopGit()

  if (!git) {
    throw new Error('Git worktree capability is unavailable in this Desktop build')
  }

  return git
}

/** A narrow naming adapter over desktopGit. It resolves desktopGit per call so
 * local/remote connection changes keep using the same authoritative routing as
 * core Desktop surfaces. */
export function createPluginWorktrees(): PluginWorktrees {
  return {
    list: async repoPath => gitCapability().worktreeList(required(repoPath, 'Repository path')),
    add: async (repoPath, options) => gitCapability().worktreeAdd(required(repoPath, 'Repository path'), options),
    remove: async (repoPath, worktreePath, options) =>
      gitCapability().worktreeRemove(
        required(repoPath, 'Repository path'),
        required(worktreePath, 'Worktree path'),
        options
      ),
    branchList: async repoPath => gitCapability().branchList(required(repoPath, 'Repository path')),
    baseBranchList: async repoPath => gitCapability().baseBranchList(required(repoPath, 'Repository path')),
    switch: async (repoPath, branch) =>
      gitCapability().branchSwitch(required(repoPath, 'Repository path'), required(branch, 'Branch'))
  }
}

/** Typed wrapper around the existing session.workspace.move RPC. */
export async function moveSessionWorkspace(
  request: GatewayRequest,
  input: MoveSessionWorkspaceInput
): Promise<MovedSessionWorkspace> {
  const sessionId = required(input.sessionId, 'Session id')
  const profile = input.profile?.trim()
  const params: Record<string, unknown> = { session_key: sessionId }

  if (input.cwd === null) {
    params.unassigned = true
  } else {
    params.cwd = required(input.cwd, 'Workspace cwd')
  }

  if (profile) {
    params.profile = profile
  }

  return request('session.workspace.move', params)
}

/** Resolve a contributed default without letting one plugin break new-chat
 * flows. Invalid/throwing contributions decline; then the caller's core
 * fallback runs. A blank fallback safely resolves to detached (`null`). */
export function resolveDefaultWorkspace(fallback: () => null | string | undefined): null | string {
  for (const contribution of registry.getArea(DEFAULT_WORKSPACE_RESOLVER_AREA)) {
    const resolver = contribution.data as DefaultWorkspaceResolverContribution | undefined

    if (typeof resolver?.resolve !== 'function') {
      continue
    }

    try {
      const resolved = resolver.resolve()?.trim()

      if (resolved) {
        return resolved
      }
    } catch {
      // A plugin resolver is advisory. Continue to the next resolver/core.
    }
  }

  try {
    return fallback()?.trim() || null
  } catch {
    return null
  }
}
