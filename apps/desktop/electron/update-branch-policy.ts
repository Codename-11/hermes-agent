export const DEPLOY_UPDATE_BRANCHES = new Set(['axiom', 'tgi'])

export interface GitCommandResult {
  code: number
  stdout?: string
}

export interface UpstreamDisparity {
  upstreamAhead?: number
  upstreamBehind?: number
  upstreamBranch?: string
  upstreamSha?: string
}

export function isDeployUpdateBranch(branch: unknown): boolean {
  return DEPLOY_UPDATE_BRANCHES.has(String(branch ?? '').trim())
}

export function manualUpdateCommand(branch: unknown): string {
  const normalized = String(branch ?? '').trim()

  if (!normalized || normalized === 'main' || isDeployUpdateBranch(normalized)) {
    return 'hermes update'
  }

  return `hermes update --branch ${normalized}`
}

/** Arguments understood by the staged bootstrap updater. */
export function stagedUpdaterBranchArgs(branch: unknown): string[] {
  const normalized = String(branch ?? '').trim() || 'main'

  return isDeployUpdateBranch(normalized) ? ['--bare-update'] : ['--branch', normalized]
}

/** Keep branch identity for logging/rebuild while selecting bare Hermes update. */
export function windowsHandoffBranchArgs(branch: unknown): string[] {
  const normalized = String(branch ?? '').trim() || 'main'
  const args = ['-Branch', normalized]

  if (isDeployUpdateBranch(normalized)) {
    args.push('-BareUpdate')
  }

  return args
}

/** Keep branch identity for logging/rebuild while selecting bare Hermes update. */
export function posixHandoffBranchArgs(branch: unknown): string[] {
  const normalized = String(branch ?? '').trim() || 'main'
  const args = ['--branch', normalized]

  if (isDeployUpdateBranch(normalized)) {
    args.push('--bare-update')
  }

  return args
}

/**
 * Best-effort fork disparity for deploy branches. Installable state remains
 * HEAD..origin/<deploy>; this only describes upstream/main...HEAD.
 */
export async function collectUpstreamDisparity(
  branch: unknown,
  runGit: (args: string[]) => Promise<GitCommandResult>,
  options: { isShallow?: boolean } = {}
): Promise<UpstreamDisparity> {
  if (!isDeployUpdateBranch(branch) || options.isShallow) {
    return {}
  }

  const remote = await runGit(['remote', 'get-url', 'upstream'])

  if (remote.code !== 0 || !String(remote.stdout ?? '').trim()) {
    return {}
  }

  const fetched = await runGit(['fetch', '--quiet', 'upstream', 'main'])

  if (fetched.code !== 0) {
    return {}
  }

  const [sha, count] = await Promise.all([
    runGit(['rev-parse', 'upstream/main']),
    runGit(['rev-list', '--left-right', '--count', 'upstream/main...HEAD'])
  ])

  if (sha.code !== 0 || count.code !== 0) {
    return {}
  }

  const upstreamSha = String(sha.stdout ?? '').trim()
  const [behindRaw, aheadRaw] = String(count.stdout ?? '').trim().split(/\s+/)
  const upstreamBehind = Number.parseInt(behindRaw ?? '', 10)
  const upstreamAhead = Number.parseInt(aheadRaw ?? '', 10)

  if (!upstreamSha || !Number.isFinite(upstreamBehind) || !Number.isFinite(upstreamAhead)) {
    return {}
  }

  return {
    upstreamAhead,
    upstreamBehind,
    upstreamBranch: 'upstream/main',
    upstreamSha
  }
}
