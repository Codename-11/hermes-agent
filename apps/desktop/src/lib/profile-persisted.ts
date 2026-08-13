import { atom, type WritableAtom } from 'nanostores'

import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile-scope'

import type { Codec } from './persisted'
import { readJson, readKey, writeJson, writeKey } from './storage'

interface ProfilePersistentOptions<T> {
  /** False for hot paths that mutate in memory and flush explicitly. */
  autoPersist?: boolean
  codec: Codec<T>
  fallback: () => T
  key: string
  /** Pre-profile storage key. Its value migrates into `default` and remains a
   * rollback-compatible mirror of the default profile only. */
  legacyKey?: string
}

export interface ProfilePersistentAtom<T> extends WritableAtom<T> {
  getForProfile(profile: string): T
  persistCurrent(): void
  setForProfile(profile: string, value: T): void
}

function loadEncodedProfiles(key: string): Record<string, string> {
  const parsed = readJson<unknown>(key)

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return {}
  }

  return Object.fromEntries(
    Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
  )
}

/** A writable atom whose visible value follows the active Desktop profile.
 * Values are kept in one profile map so switching is synchronous and does not
 * briefly expose another profile's workspace state. */
export function profilePersistentAtom<T>({
  autoPersist = true,
  codec,
  fallback,
  key,
  legacyKey
}: ProfilePersistentOptions<T>): ProfilePersistentAtom<T> {
  const encodedByProfile = loadEncodedProfiles(key)
  const legacy = legacyKey ? readKey(legacyKey) : null

  if (encodedByProfile.default === undefined && legacy !== null) {
    encodedByProfile.default = legacy
    writeJson(key, encodedByProfile)
  }

  const decode = (profile: string): T => {
    const raw = encodedByProfile[profile]

    if (raw === undefined) {
      return fallback()
    }

    try {
      return codec.decode(raw)
    } catch {
      return fallback()
    }
  }

  let activeProfile = normalizeProfileKey($activeGatewayProfile.get())
  let switching = false
  const $value = atom<T>(decode(activeProfile))

  const persistForProfile = (profile: string, value: T) => {
    const encoded = codec.encode(value)

    if (encoded === null) {
      delete encodedByProfile[profile]
    } else {
      encodedByProfile[profile] = encoded
    }

    writeJson(key, Object.keys(encodedByProfile).length === 0 ? null : encodedByProfile)

    if (legacyKey && profile === 'default') {
      writeKey(legacyKey, encoded)
    }
  }

  const persistValue = (value: T) => {
    if (autoPersist && !switching) {
      persistForProfile(activeProfile, value)
    }
  }

  $value.listen(persistValue)

  $activeGatewayProfile.listen(profile => {
    const nextProfile = normalizeProfileKey(profile)

    if (nextProfile === activeProfile) {
      return
    }

    activeProfile = nextProfile
    switching = true
    $value.set(decode(activeProfile))
    switching = false
  })

  return Object.assign($value, {
    getForProfile: (profile: string) => decode(normalizeProfileKey(profile)),
    persistCurrent: () => persistForProfile(activeProfile, $value.get()),
    setForProfile: (profile: string, value: T) => {
      const key = normalizeProfileKey(profile)

      persistForProfile(key, value)

      if (key === activeProfile) {
        switching = true
        $value.set(value)
        switching = false
      }
    }
  })
}
