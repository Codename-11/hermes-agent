import { useStore } from '@nanostores/react'

import { $activeConnectionId } from '@/store/connections'
import { $profiles } from '@/store/profile'
import type { ProfileInfo } from '@/types/hermes'

import { filterCoreProfiles, PROFILE_VISIBILITY_AREA } from '../profile-visibility'

import { useContributions } from './use-contributions'

/** Reactive projection for core profile chrome. Routing and backend stores keep
 * using `$profiles`; only the UI surfaces that opt in are filtered. */
export function useCoreProfiles(): ProfileInfo[] {
  const profiles = useStore($profiles)
  const connectionId = useStore($activeConnectionId)
  const contributions = useContributions(PROFILE_VISIBILITY_AREA)

  return filterCoreProfiles(profiles, connectionId, contributions)
}