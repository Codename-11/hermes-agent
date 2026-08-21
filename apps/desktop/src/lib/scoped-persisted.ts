import { atom, type WritableAtom } from 'nanostores'

import { type Codec, Codecs } from './persisted'
import { readKey, writeKey } from './storage'

interface ScopedEntry<T> {
  $value: WritableAtom<T>
  applying: boolean
  autoPersist: boolean
  codec: Codec<T>
  fallback: T
  key: string
}

export interface ScopedPersistentAtom<T> extends WritableAtom<T> {
  persistCurrent(): void
}

export interface ScopedPersistentAtomOptions {
  autoPersist?: boolean
}

export interface ScopedPersistence<Scope> {
  activeScope(): Scope | undefined
  scopedPersistentAtom<T>(
    key: string,
    fallback: T,
    codec?: Codec<T>,
    options?: ScopedPersistentAtomOptions
  ): ScopedPersistentAtom<T>
  setScope(scope: Scope): void
}

export interface ScopedPersistenceOptions<Scope> {
  initialScope?: Scope
  storageKey(key: string, scope: Scope): string
}

/**
 * Build an isolated family of persistent atoms whose storage keys are derived
 * from an explicit scope. Families do not share a registry: connection-bound
 * stores may rescope freely while a window workspace can pin its family once.
 */
export function createScopedPersistence<Scope>({
  initialScope,
  storageKey
}: ScopedPersistenceOptions<Scope>): ScopedPersistence<Scope> {
  let scope = initialScope
  const entries: ScopedEntry<any>[] = []

  const load = <T>(entry: ScopedEntry<T>, nextScope: Scope): T => {
    const raw = readKey(storageKey(entry.key, nextScope))

    if (raw === null) {
      return entry.fallback
    }

    try {
      return entry.codec.decode(raw)
    } catch {
      return entry.fallback
    }
  }

  const scopedPersistentAtom = <T>(
    key: string,
    fallback: T,
    codec: Codec<T> = Codecs.json<T>(),
    { autoPersist = true }: ScopedPersistentAtomOptions = {}
  ): ScopedPersistentAtom<T> => {
    const entry: ScopedEntry<T> = { $value: atom(fallback), applying: false, autoPersist, codec, fallback, key }

    if (scope !== undefined) {
      entry.$value.set(load(entry, scope))
    }

    entries.push(entry)
    entry.$value.listen(value => {
      if (!entry.autoPersist || entry.applying || scope === undefined) {
        return
      }

      writeKey(storageKey(entry.key, scope), entry.codec.encode(value))
    })

    return Object.assign(entry.$value, {
      persistCurrent: () => {
        if (scope !== undefined) {
          writeKey(storageKey(entry.key, scope), entry.codec.encode(entry.$value.get()))
        }
      }
    })
  }

  const setScope = (nextScope: Scope): void => {
    if (Object.is(scope, nextScope)) {
      return
    }

    scope = nextScope

    for (const entry of entries) {
      entry.applying = true
    }

    try {
      for (const entry of entries) {
        entry.$value.set(load(entry, nextScope))
      }
    } finally {
      for (const entry of entries) {
        entry.applying = false
      }
    }
  }

  return { activeScope: () => scope, scopedPersistentAtom, setScope }
}
