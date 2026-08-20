/**
 * SESSION DOT STATE — one map from session id to the single status a surface
 * should paint, so the sidebar, the pane tabs, and the switcher can never
 * disagree about what a session is doing.
 *
 * It exists for two reasons the individual membership atoms cannot cover:
 *
 * 1. PRIORITY IN ONE PLACE. The signals overlap — a session can be working AND
 *    unread AND running a background job — and resolving that per call site is
 *    how surfaces drift apart.
 * 2. LINEAGE. Compression rotates a conversation's stored id, so a sidebar row,
 *    a persisted tile, and the route can each hold a different tip of one
 *    lineage. Every state is claimed under every alias (see `lineageAliases`),
 *    so a surface gets the right answer whichever tip it happens to hold.
 *
 * The inputs are all reference-stable across stream deltas, so this recomputes
 * on status edges rather than per token.
 *
 * Unread has TWO sources, both claiming the same state: the runtime marker
 * (a turn finished in the background while this window wasn't looking at it,
 * $unreadFinishedSessionIds — transient) and the backend's derived read-state
 * watermark (row.unread — persists across restarts and is visible to every
 * surface). The write side of the persisted flag lives in session-unread.ts.
 */

import { computed } from 'nanostores'

import { stableArray, stableRecord } from '@/lib/stable-array'

import { $backgroundRunningSessionIds } from './composer-status'
import { $activeGatewayProfile } from './profile-scope'
import { $cronSessions, $messagingSessions, $sessions, $unreadFinishedSessionIds, lineageAliases } from './session'
import {
  $attentionSessionKeys,
  $draftSessionIds,
  $sessionStates,
  $stalledSessionIds,
  $workingSessionKeys,
  sessionStatusKey
} from './session-states'
import { $unreadWriteGuard, UNREAD_WRITE_GUARD_MS, unreadWriteGuardKey } from './session-unread-remote'
import { $subagentsBySession, activeSubagentCount } from './subagents'

let delegatingIds: readonly string[] = []
export const $delegatingSessionIds = computed(
  [$subagentsBySession, $sessionStates, $sessions],
  (bySession, states, sessions) => {
    const ids = new Set<string>()

    for (const [runtimeId, items] of Object.entries(bySession)) {
      if (activeSubagentCount(items) === 0) {continue}

      for (const alias of lineageAliases(states[runtimeId]?.storedSessionId ?? runtimeId, sessions)) {ids.add(alias)}
    }

    return (delegatingIds = stableArray(delegatingIds, [...ids]))
  }
)

export type SessionDotState = 'background' | 'draft' | 'idle' | 'needs-input' | 'stalled' | 'unread' | 'working'

/** The sidebar row's arc. A quiet turn is still authoritatively running, so
 *  `stalled` keeps it; a blocking prompt drops it, because the amber dot is the
 *  louder cue and two treatments at once fight each other. */
export const showsRunningArc = (state: SessionDotState): boolean => state === 'stalled' || state === 'working'

/** Whether this turn is the session's own, live: brighter title, and the row's
 *  age yields to the actions menu. Wider than the arc — a turn waiting on an
 *  answer has not ended. */
export const hasLiveTurn = (state: SessionDotState): boolean => showsRunningArc(state) || state === 'needs-input'

/** The buckets the sidebar's status filter and ordering work in. `stalled` and
 *  `background` fold into the state a user would name them. */
export type SessionStatusBucket = 'draft' | 'idle' | 'needs-input' | 'unread' | 'working'

export const sessionStatusBucket = (state: SessionDotState = 'idle'): SessionStatusBucket =>
  state === 'stalled' || state === 'background' ? 'working' : state

const STATUS_RANK: Record<SessionStatusBucket, number> = {
  'needs-input': 0,
  working: 1,
  unread: 2,
  draft: 3,
  idle: 4
}

/** Loudest first — what ordering by status sorts on. */
export const sessionStatusRank = (state?: SessionDotState): number => STATUS_RANK[sessionStatusBucket(state)]

let dotStates: Readonly<Record<string, SessionDotState>> = {}

export const $sessionDotStateById = computed(
  [
    $attentionSessionKeys,
    $workingSessionKeys,
    $stalledSessionIds,
    $backgroundRunningSessionIds,
    $delegatingSessionIds,
    $unreadFinishedSessionIds,
    $draftSessionIds,
    $sessions,
    $unreadWriteGuard,
    $activeGatewayProfile
  ],
  (attention, working, stalled, background, delegating, unread, draft, sessions, unreadWriteGuard, activeProfile) => {
    const next: Record<string, SessionDotState> = {}

    const scopedAliases = (session: (typeof sessions)[number]) =>
      lineageAliases(
        session.id,
        sessions.filter(
          candidate => (candidate.profile?.trim() || 'default') === (session.profile?.trim() || 'default')
        )
      )

    const claimBare = (ids: readonly string[], state: SessionDotState) => {
      const members = new Set(ids)

      for (const session of sessions) {
        if (scopedAliases(session).some(alias => members.has(alias))) {
          next[sessionStatusKey(session.profile, session.id)] = state
        }
      }
    }

    const claimKeys = (keys: readonly string[], state: SessionDotState) => {
      for (const key of keys) {
        next[key] = state
      }
    }

    const claimRows = (rows: readonly (typeof sessions)[number][], state: SessionDotState) => {
      const members = new Set(rows.map(session => sessionStatusKey(session.profile, session.id)))

      for (const session of sessions) {
        if (scopedAliases(session).some(alias => members.has(sessionStatusKey(session.profile, alias)))) {
          next[sessionStatusKey(session.profile, session.id)] = state
        }
      }
    }

    // Weakest claim first — each pass overwrites the one above it, so the order
    // below IS the priority order. A blocking prompt outranks everything: it is
    // the only state that needs the user.
    //
    // Draft is weakest of all: it says only "no turn has happened here yet", so
    // the first thing that does happen speaks over it.
    claimBare(draft, 'draft')
    claimBare(unread, 'unread')

    const persistedUnread: (typeof sessions)[number][] = []

    for (const session of sessions) {
      const entry = unreadWriteGuard.get(unreadWriteGuardKey(session.id, session.profile))

      if (entry && Date.now() - entry.at < UNREAD_WRITE_GUARD_MS) {
        if (entry.value) {persistedUnread.push(session)}

        continue
      }

      if (session.unread === true) {persistedUnread.push(session)}
    }

    claimRows(persistedUnread, 'unread')
    claimBare(background, 'background')
    claimBare(delegating, 'background')
    claimKeys(working, 'working')

    // Stalled REFINES working rather than rivalling it — the turn is still
    // authoritatively running, it has just gone quiet — so it only downgrades a
    // session already claimed as working. The hint outlives its turn by a tick
    // on some paths; without this it could invent a running session.
    const stalledIds = new Set(stalled)

    for (const session of sessions) {
      const key = sessionStatusKey(session.profile, session.id)

      if (next[key] === 'working' && scopedAliases(session).some(alias => stalledIds.has(alias))) {
        next[key] = 'stalled'
      }
    }

    claimKeys(attention, 'needs-input')

    // Profile-qualified keys keep duplicate stored ids isolated. Preserve the
    // store's original bare-id contract as an active-profile compatibility
    // view so legacy consumers never observe another profile's status.
    const activePrefix = sessionStatusKey(activeProfile, '')

    for (const [key, state] of Object.entries(next)) {
      if (key.startsWith(activePrefix)) {next[key.slice(activePrefix.length)] = state}
    }

    return (dotStates = stableRecord(dotStates, next))
  }
)

/** Listed, non-archived rows whose resolved status is unread. Alias keys in
 *  `$sessionDotStateById` are ignored unless they are themselves a listed row. */
export function unreadSessionCount(
  byId: Readonly<Record<string, SessionDotState>>,
  ...lists: Array<readonly { archived?: boolean; id: string }[]>
): number {
  let n = 0

  for (const rows of lists) {
    for (const row of rows) {
      if (!row.archived && byId[row.id] === 'unread') {
        n++
      }
    }
  }

  return n
}

export const $unreadSessionCount = computed(
  [$sessionDotStateById, $sessions, $cronSessions, $messagingSessions],
  (byId, sessions, cron, messaging) => unreadSessionCount(byId, sessions, cron, messaging)
)
