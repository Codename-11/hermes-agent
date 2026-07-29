/**
 * Pure derivation of how the app names an update target: the label
 * (`v0.4.2`, `backend v0.4.2 (+12)`, `v0.4.2 · update`), its tooltip, and
 * whether an update is waiting.
 *
 * The statusbar and the command palette both name the same two targets, so the
 * wording lives here once — a palette row and its statusbar item can't drift
 * into describing the same install differently.
 */

import type { UpdateTarget } from '@/lib/update-copy'

export interface VersionStatusCopy {
  backendLabel: (version: string) => string
  backendVersion: (version: string) => string
  branch: (branch: string) => string
  clientLabel: (version: string) => string
  commit: (sha: string) => string
  commitsBehind: (count: number, branch: string) => string
  desktopVersion: (version: string) => string
  restart: string
  unknown: string
  update: string
  updateInProgress: string
}

export interface VersionStatusInput {
  /** True while an apply is in flight (including the restart hand-off). */
  applying: boolean
  /** Latest line from the apply stream — leads the tooltip while applying. */
  applyMessage?: string
  backendMessage?: string
  behind?: number
  branch?: string
  copy: VersionStatusCopy
  deployBehind?: number
  deployBranch?: string
  /** Remote mode: the client is one of two versions on screen, so it says so. */
  remote: boolean
  /** The apply reached the restart stage — labels `restart`, not `update`. */
  restarting: boolean
  /** Client only: short commit sha of the running build. */
  sha?: null | string
  target: UpdateTarget
  upstreamAhead?: number
  upstreamBehind?: number
  upstreamBranch?: string
  /** Backend only: an update the commit count can't express (pip installs). */
  updateAvailable?: boolean
  version?: null | string
}

export interface VersionStatusResult {
  /** Secondary text beside the label — the commit sha, when it adds anything. */
  detail?: string
  /** An update is waiting: callers tint the row with it. */
  hasUpdate: boolean
  label: string
  tooltip?: string
  /** Nothing identifies this target yet — callers hide the row. */
  unknown: boolean
}

export function resolveVersionStatus({
  applyMessage,
  applying,
  backendMessage,
  behind = 0,
  branch,
  copy,
  deployBehind = 0,
  deployBranch,
  remote,
  restarting,
  sha = null,
  target,
  upstreamAhead = 0,
  upstreamBehind = 0,
  upstreamBranch,
  updateAvailable,
  version = null
}: VersionStatusInput): VersionStatusResult {
  const client = target === 'client'
  const busy = applying || restarting
  const available = behind > 0 || (!client && !!updateAvailable)

  // A client with no version still identifies itself by sha; a backend can't.
  const named = version ?? (client ? sha : null) ?? copy.unknown

  const base = !client
    ? copy.backendLabel(named)
    : remote
      ? copy.clientLabel(named)
      : (version && `v${version}`) || named

  // Commits behind is the precise diff; `(update)` is the fallback for a
  // backend that knows it's stale but can't count (pip, non-git checkout).
  const hint = busy ? '' : behind > 0 ? ` (+${behind})` : available ? ` (${copy.update})` : ''

  const pending = [
    deployBehind > 0 && `${deployBehind} from ${deployBranch ?? 'deploy branch'}`,
    upstreamBehind > 0 && `${upstreamBehind} from ${upstreamBranch ?? 'upstream/main'}`
  ].filter(Boolean)
  const disparity = upstreamBranch
    ? `${upstreamBranch}: ${[
        upstreamAhead > 0 && `+${upstreamAhead} carried`,
        upstreamBehind > 0 && `${upstreamBehind} behind`,
        upstreamAhead <= 0 && upstreamBehind <= 0 && 'aligned'
      ]
        .filter(Boolean)
        .join(', ')}`
    : null

  const tooltip = [
    busy && (applyMessage || copy.updateInProgress),
    !busy && pending.length > 0 && `Pending backend update: ${pending.join(', ')}`,
    !busy && pending.length === 0 && behind > 0 && copy.commitsBehind(behind, (client ? branch : 'main') || '...'),
    !busy && behind <= 0 && available && copy.update,
    version && (client ? copy.desktopVersion(version) : copy.backendVersion(version)),
    client && sha && copy.commit(sha),
    client && branch && copy.branch(branch),
    !busy && disparity,
    !busy && backendMessage
  ]
    .filter(Boolean)
    .join(' · ')

  return {
    detail:
      !busy && disparity && (upstreamAhead > 0 || upstreamBehind > 0)
        ? disparity.replace(`${upstreamBranch}: `, '')
        : client && version && sha && !busy && !remote
          ? sha
          : undefined,
    hasUpdate: !busy && available,
    label: busy ? `${base} · ${restarting ? copy.restart : copy.update}` : `${base}${hint}`,
    tooltip: tooltip || undefined,
    unknown: !version && !(client && sha)
  }
}
