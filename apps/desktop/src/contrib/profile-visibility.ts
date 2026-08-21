import type { ProfileInfo } from '@/types/hermes'

import type { Contribution } from './types'

/** Data-only area for plugins that own profiles rendered elsewhere. Core keeps
 * the authoritative profile inventory intact and filters presentation only. */
export const PROFILE_VISIBILITY_AREA = 'profiles.visibility'

export interface ProfileVisibilityIdentity {
  /** Registry route identity; profile names alone are ambiguous across sources. */
  connectionId: string
  profile: string
}

export interface ProfileVisibilityContribution {
  hidden: readonly ProfileVisibilityIdentity[]
}

const hiddenIdentity = (value: unknown): ProfileVisibilityIdentity | null => {
  if (!value || typeof value !== 'object') {
    return null
  }

  const { connectionId, profile } = value as Partial<ProfileVisibilityIdentity>
  const source = typeof connectionId === 'string' ? connectionId.trim() : ''
  const name = typeof profile === 'string' ? profile.trim() : ''

  return source && name ? { connectionId: source, profile: name } : null
}

/** Resolve the core-visible projection for one route. Only plugin-owned,
 * route-qualified declarations participate; malformed data and core entries
 * fail open. The input array is never mutated. */
export function filterCoreProfiles(
  profiles: readonly ProfileInfo[],
  connectionId: null | string,
  contributions: readonly Contribution[]
): ProfileInfo[] {
  const route = (connectionId ?? '').trim()

  if (!route) {
    return [...profiles]
  }

  const hidden = new Set<string>()

  for (const contribution of contributions) {
    if (!contribution.source?.startsWith('plugin:')) {
      continue
    }

    const candidates = (contribution.data as Partial<ProfileVisibilityContribution> | null)?.hidden

    if (!Array.isArray(candidates)) {
      continue
    }

    for (const candidate of candidates) {
      const identity = hiddenIdentity(candidate)

      if (identity?.connectionId === route) {
        hidden.add(identity.profile)
      }
    }
  }

  return profiles.filter(profile => profile.is_default || !hidden.has(profile.name))
}
