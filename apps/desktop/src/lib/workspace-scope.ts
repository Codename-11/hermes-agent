import { windowProfileOverride } from '@/store/windows'

import { type Codec, Codecs } from './persisted'
import {
  createScopedPersistence,
  type ScopedPersistentAtom,
  type ScopedPersistentAtomOptions
} from './scoped-persisted'

const normalizeWorkspaceScope = (profile: string | null | undefined): string => profile?.trim() || 'default'

const explicitWindowScope = windowProfileOverride()
let workspacePinned = explicitWindowScope !== null
const workspacePersistence = createScopedPersistence<string>({
  // The bare/default namespace remains the provisional seed until boot adopts
  // the authoritative profile. This preserves synchronous store construction
  // and legacy default-profile persistence without treating that seed as the
  // window's final workspace scope.
  initialScope: normalizeWorkspaceScope(explicitWindowScope),
  storageKey: (key, scope) =>
    scope === 'default' ? key : `${key}.profile.${encodeURIComponent(normalizeWorkspaceScope(scope))}`
})

/** The immutable profile namespace selected for this renderer window. */
export function activeWorkspaceScope(): string | undefined {
  return workspacePinned ? workspacePersistence.activeScope() : undefined
}

/**
 * Pin this window's presentation workspace. The explicit URL override is
 * resolved at module load; ordinary windows call this once with the profile
 * authoritatively adopted during boot. Later gateway/profile/session switches
 * are request-routing events and cannot re-home the workspace.
 */
export function initializeWorkspaceScope(profile: string | null | undefined): string {
  if (workspacePinned) {
    return workspacePersistence.activeScope() ?? 'default'
  }

  const scope = normalizeWorkspaceScope(profile)
  workspacePersistence.setScope(scope)
  workspacePinned = true

  return scope
}

export function workspaceScopedAtom<T>(
  key: string,
  fallback: T,
  codec: Codec<T> = Codecs.json<T>(),
  options?: ScopedPersistentAtomOptions
): ScopedPersistentAtom<T> {
  return workspacePersistence.scopedPersistentAtom(key, fallback, codec, options)
}
