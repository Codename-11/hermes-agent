/**
 * Persisted unread flag sync (backend read-state watermark via
 * PATCH /api/sessions/{id} → SessionDB.set_session_read).
 *
 * The sidebar's dot is fed by TWO sources (see session-dot-state.ts): the
 * runtime "turn finished in background" marker ($unreadFinishedSessionIds,
 * transient) and the backend's derived `unread` key (last_read_at watermark
 * vs last_active — survives restarts and is visible to every surface). This
 * module owns the WRITE side of the persisted flag: the row-level
 * "Mark as unread"/"Mark as read" toggle and the automatic clear when a
 * session is opened. The read side lives in session-dot-state.ts.
 *
 * Optimistic, then honest (AGENTS.md): paint the row immediately, PATCH the
 * backend, roll back visibly on failure. A list page already in flight when
 * we PATCH can land after the ack carrying the OLD value — the write guard
 * lets our value outrank that stale page briefly (#74570 pattern, same as
 * session-pin-sync.ts).
 *
 * NOTE: import cycle with ./session is inert — both modules only touch each
 * other's exports inside function bodies, never at module evaluation time.
 */
import { atom } from 'nanostores'

import { setSessionUnreadRemote } from '@/hermes'

import { normalizeProfileKey } from './profile-scope'
import { $sessions, setSessions } from './session'

export const UNREAD_WRITE_GUARD_MS = 10_000

interface UnreadWriteGuardEntry {
  at: number
  profile: string
  storedId: string
  value: boolean
}

/** profile + id -> the value we wrote and when. Guarded rows outrank list pages. */
export const $unreadWriteGuard = atom<Map<string, UnreadWriteGuardEntry>>(new Map())

export const unreadWriteGuardKey = (storedId: string, profile: null | string | undefined) =>
  `${normalizeProfileKey(profile)}\u0000${storedId}`

function rowFor(storedId: string, profile?: string) {
  const matches = $sessions.get().filter(row => row.id === storedId)

  if (profile !== undefined) {
    const normalized = normalizeProfileKey(profile)

    return matches.find(row => normalizeProfileKey(row.profile) === normalized)
  }

  return matches.length === 1 ? matches[0] : undefined
}

/** Toggle the persisted unread flag: optimistic row update, then PATCH, then
 *  roll back visibly if the write fails. No-op for runtime-only sessions (a
 *  brand-new chat with no persisted row yet — there is nothing to flag). */
export async function markSessionUnread(storedId: string, unread: boolean, profile?: string): Promise<void> {
  const row = rowFor(storedId, profile)

  if (!row) {
    return
  }

  const normalizedProfile = normalizeProfileKey(row.profile)
  const guardKey = unreadWriteGuardKey(storedId, normalizedProfile)
  const guard = new Map($unreadWriteGuard.get())
  guard.set(guardKey, { at: Date.now(), profile: normalizedProfile, storedId, value: unread })
  $unreadWriteGuard.set(guard)

  setSessions(rows =>
    rows.map(r =>
      r.id === storedId && normalizeProfileKey(r.profile) === normalizedProfile ? { ...r, unread } : r
    )
  )

  try {
    await setSessionUnreadRemote(storedId, unread, row.profile)
  } catch (err) {
    // Roll back visibly: the backend kept the old value.
    const guard2 = new Map($unreadWriteGuard.get())
    guard2.delete(guardKey)
    $unreadWriteGuard.set(guard2)
    setSessions(rows =>
      rows.map(r =>
        r.id === storedId && normalizeProfileKey(r.profile) === normalizedProfile ? { ...r, unread: !unread } : r
      )
    )
    throw err
  }
}

/** Opening a session clears its persisted unread flag (auto-mark-read).
 *  Best-effort: a failed PATCH is healed by the next honest refresh. */
export async function clearUnreadOnOpen(storedId: string, profile?: string): Promise<void> {
  const row = rowFor(storedId, profile)

  if (!row || row.unread !== true) {
    return
  }

  try {
    await markSessionUnread(storedId, false, row.profile)
  } catch {
    // Ignore: the dot simply returns until a refresh reconciles.
  }
}

/** Release guard entries once a list page confirms the value we wrote. Call
 *  once at boot, next to watchSessionPins(). */
export function watchUnreadWriteGuard(): void {
  $sessions.listen(rows => {
    const guard = $unreadWriteGuard.get()
    let changed = false

    for (const [key, entry] of guard) {
      const row = rows.find(
        r => r.id === entry.storedId && normalizeProfileKey(r.profile) === entry.profile
      )

      if (row && row.unread === entry.value) {
        guard.delete(key)
        changed = true
      }
    }

    if (changed) {
      $unreadWriteGuard.set(new Map(guard))
    }
  })
}
