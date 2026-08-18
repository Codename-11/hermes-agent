/**
 * MULTI-SESSION VIEW STATE — the reactive face of the per-runtime session
 * cache (`sessionStateByRuntimeIdRef` in use-session-state-cache).
 *
 * The cache already ingests EVERY session's gateway events; only the view
 * was single-session ($messages + the active-id gate). This store mirrors
 * the cache per runtime id so any number of surfaces (session tiles, future
 * pane windows) can each subscribe to one session's state without touching
 * the main chat's `$messages` pipeline — same pattern as `useSessionSlice`
 * over `$todosBySession`, applied to whole `ClientSessionState`s.
 *
 * TILES are the first consumer: sessions opened side-by-side with the main
 * thread, each in its own layout-tree pane. `$sessionTiles` holds the
 * stored-session ids (persisted — tiles survive restarts); the wiring layer
 * owns resume/submit (it has the gateway + cache internals) and registers
 * itself here as the delegate so tile UI stays dependency-light.
 */

import { registryBackendScopeKey } from '@hermes/shared'
import { atom, computed } from 'nanostores'

import type { ClientSessionState } from '@/app/types'
import { allPaneIds, findGroup, findGroupOfPane, type LayoutNode } from '@/components/pane-shell/tree/model'
import {
  $activeTreeGroup,
  $layoutTree,
  focusedSessionTabAnchor,
  moveTreePane,
  noteActiveTreeGroup,
  revealTreePane
} from '@/components/pane-shell/tree/store'
import { stableArray } from '@/lib/stable-array'
import { readJson, writeJson } from '@/lib/storage'
import type { SessionInfo } from '@/types/hermes'

import { $activeGatewayProfile, normalizeProfileKey } from './profile'
import { clearAllProviderWaits, clearSessionProviderWait } from './provider-wait'
import {
  $activeSessionId,
  $lastReadAtBySessionId,
  $selectedStoredSessionId,
  $sessions,
  clearReadBaseline,
  lineageAliases,
  markSessionRead,
  sessionMatchesStoredId,
  setActiveSessionStoredIdRotation,
  setSessions
} from './session'
import { ackStoredSessionId, markSessionUnreadFinished } from './session-unread'
import { isSecondaryWindow } from './windows'

// ---------------------------------------------------------------------------
// Reactive per-runtime session state (view mirror of the wiring cache).
// ---------------------------------------------------------------------------

export const $sessionStates = atom<Record<string, ClientSessionState>>({})

// ---------------------------------------------------------------------------
// Event-source scopes: which registry connection's socket delivered a runtime
// session's events. Working/attention membership alone is profile-blind — two
// connected gateways can both expose a 'default' profile, so the gateway
// keep-set (pruneSecondaryGateways) must key live work by the composite
// (connectionId, profile) scope, not the bare profile name. Recorded at
// event fan-in (use-gateway-boot); local/primary events carry no connectionId
// and record nothing, so single-source behavior is untouched.
// ---------------------------------------------------------------------------

const sessionScopeByRuntimeId = new Map<string, string>()

export function recordSessionEventScope(event: { connectionId?: string; profile?: string; session_id?: string }): void {
  if (event.session_id && event.connectionId) {
    sessionScopeByRuntimeId.set(event.session_id, registryBackendScopeKey(event.connectionId, event.profile))
  }
}

/** Composite scopes of registry-sourced sessions that are live (busy or
 * waiting on input) — the (connectionId, profile) half of the gateway
 * keep-set. Local-source live work keeps flowing through profile names. */
export function liveSessionScopes(): Set<string> {
  const scopes = new Set<string>()

  for (const [runtimeId, state] of Object.entries($sessionStates.get())) {
    if (!state || (!state.busy && !state.needsInput)) {
      continue
    }

    const scope = sessionScopeByRuntimeId.get(runtimeId)

    if (scope) {
      scopes.add(scope)
    }
  }

  return scopes
}

// Stored session ids whose authoritative state is still busy, but whose
// runtime has produced no state publish for the watchdog window. Silence is
// not completion: long tool calls can legitimately stay quiet, so this is a
// presentation hint and never mutates the backend-derived busy state.
export const $stalledSessionIds = atom<string[]>([])

export function setSessionStalled(storedSessionId: string | null | undefined, stalled: boolean) {
  if (!storedSessionId) {
    return
  }

  const current = $stalledSessionIds.get()
  const present = current.includes(storedSessionId)

  if (stalled && !present) {
    $stalledSessionIds.set([...current, storedSessionId])
  } else if (!stalled && present) {
    $stalledSessionIds.set(current.filter(id => id !== storedSessionId))
  }
}

// --- Watchdog: marks busy sessions quiet after a long stream silence -------
// Tuned against what this app actually does rather than a round number: a
// typecheck or a full test run here goes quiet for minutes at a stretch and is
// perfectly healthy, so anything under ~4 min would paint normal work as
// suspect. Eight minutes was the other failure — longer than a user is willing
// to sit and wonder, so the hint arrived after they had already given up on it.
export const SESSION_WATCHDOG_TIMEOUT_MS = 5 * 60 * 1000
const sessionWatchdogTimers = new Map<string, ReturnType<typeof setTimeout>>()

function armWatchdog(runtimeId: string) {
  const existing = sessionWatchdogTimers.get(runtimeId)

  if (existing) {
    clearTimeout(existing)
  }

  sessionWatchdogTimers.set(
    runtimeId,
    setTimeout(() => {
      sessionWatchdogTimers.delete(runtimeId)
      const current = $sessionStates.get()[runtimeId]

      if (current?.busy) {
        setSessionStalled(current.storedSessionId, true)
      }
    }, SESSION_WATCHDOG_TIMEOUT_MS)
  )
}

function clearWatchdog(runtimeId: string) {
  const t = sessionWatchdogTimers.get(runtimeId)

  if (t) {
    clearTimeout(t)
    sessionWatchdogTimers.delete(runtimeId)
  }
}

// --- Settle grace: keeps a just-finished session in the sidebar merge set ---
const SESSION_SETTLE_GRACE_MS = 30 * 1000
const settledExpiry = new Map<string, number>()

function markSettled(storedId: string) {
  settledExpiry.set(storedId, Date.now() + SESSION_SETTLE_GRACE_MS)
}

function clearSettled(storedId: string) {
  settledExpiry.delete(storedId)
}

/** Stored ids whose turn ended within the grace window. Prunes expired. */
export function getRecentlySettledSessionIds(now: number = Date.now()): string[] {
  const live: string[] = []

  for (const [id, expiry] of settledExpiry) {
    if (expiry > now) {
      live.push(id)
    } else {
      settledExpiry.delete(id)
    }
  }

  return live
}

// --- Transition detection (called automatically from publishSessionState) ---
function handleTransition(previous: ClientSessionState | null, next: ClientSessionState, runtimeId: string) {
  // Compression id rotation: signal the route-follow effect with enough
  // provenance (previous id + runtime) that the consumer can reject the event
  // if the user navigated elsewhere before React handled it. A bare next id
  // could let a background session's delayed rotation steal the foreground
  // route.
  if (previous?.storedSessionId && next.storedSessionId && previous.storedSessionId !== next.storedSessionId) {
    if (runtimeId === $activeSessionId.get()) {
      setActiveSessionStoredIdRotation({
        nextStoredSessionId: next.storedSessionId,
        previousStoredSessionId: previous.storedSessionId,
        runtimeSessionId: runtimeId
      })
    }

    clearSettled(previous.storedSessionId)
    setSessionStalled(previous.storedSessionId, false)
  }

  // Every busy publish is stream activity: clear the quiet hint and restart
  // the silence window. A real terminal transition clears both the timer and
  // any hint, but only that authoritative transition clears working/busy.
  if (next.busy) {
    setSessionStalled(next.storedSessionId, false)
    armWatchdog(runtimeId)
  } else {
    clearWatchdog(runtimeId)
    setSessionStalled(next.storedSessionId, false)
    setSessionStalled(previous?.storedSessionId, false)
  }

  const storedId = next.storedSessionId

  if (!storedId) {
    return
  }

  const wasWorking = previous?.busy ?? false

  if (next.busy && !wasWorking) {
    clearSettled(storedId)
    // A NEW turn is starting: the read baseline guarded the PREVIOUS
    // completion's re-asserts. Dropping it here means this turn's finish
    // re-lights even if it lands within the same millisecond as the last
    // read (same-tick submit → finish in tests and fast local models).
    clearReadBaseline(storedId)
  } else if (!next.busy && wasWorking) {
    markSettled(storedId)

    // FOCUSED, not selected: a session finishing in the tile the user is
    // watching is already seen, and a tile is never the primary selection.
    if (storedId !== $focusedStoredSessionId.get()) {
      // Re-light only genuinely new completions: if the user already viewed
      // this session (or its family) at or after this settle moment, a
      // re-assert of the same completion must not re-arm the dot. `-1` for
      // "never read" (not `0`) so fake-timer tests pinned to t=0 still light.
      const lastReadAt = $lastReadAtBySessionId.get()[storedId] ?? -1

      if (Date.now() > lastReadAt) {
        // Flags the transient atom AND persists a marker, so the green dot
        // survives an app restart (see session-unread.ts).
        markSessionUnreadFinished(storedId)
      }
    }
  }
}

/** Is any surface on THIS window still holding the runtime — the primary view
 *  or an open tile? (A tile mid-resume references by stored id only; its
 *  runtime binding is patched in after `resumeTile` returns.) */
function runtimeReferenced(runtimeId: string, storedSessionId: null | string): boolean {
  if (runtimeId === $activeSessionId.get()) {
    return true
  }

  return $sessionTiles
    .get()
    .some(
      tile =>
        tile.runtimeId === runtimeId ||
        (storedSessionId !== null &&
          tile.storedSessionId === storedSessionId &&
          normalizeProfileKey(tile.profile) ===
            normalizeProfileKey($sessionStates.get()[runtimeId]?.profile ?? $activeGatewayProfile.get()))
    )
}

/** A state no surface needs anymore: its turn is over (not busy, not waiting
 *  on the user) and neither the primary view nor any tile holds the runtime.
 *  `needsInput` states stay — the sidebar's attention dot reads them. */
function evictable(runtimeId: string, state: ClientSessionState): boolean {
  return (
    !state.busy && !state.needsInput && !state.awaitingResponse && !runtimeReferenced(runtimeId, state.storedSessionId)
  )
}

/** Publish one session's state. Automatically fires transition side-effects
 *  (watchdog arm/disarm, settle grace, unread marker, compression id rotation)
 *  by diffing previous vs next — callers never need to manually call a
 *  transition handler.
 *
 *  Skips the publish when the new state is identical to the existing one
 *  (same reference) to avoid churning `$sessionStates` on periodic
 *  `session.info` heartbeats that carry no change — otherwise every ~1/s
 *  heartbeat creates a new Record spread, triggering computed atoms
 *  ($workingSessionIds, $attentionSessionIds) and their subscribers
 *  unnecessarily. The runtime-id→state cache (sessionStateByRuntimeIdRef)
 *  is updated independently by the caller, so the visual path stays live
 *  without the store churn.
 *
 *  A settled state nothing references releases its transcript instead of
 *  republishing it. Gateway events keep flowing for sessions whose tile was
 *  closed mid-turn, and parking each one's full transcript here forever is the
 *  leak that made the app crawl after a day of tile use. Transition side
 *  effects still fire, so lightweight status and the unread dot survive. A
 *  FIRST publish always lands in full because a resume can publish its idle
 *  state a beat before `$activeSessionId` / the tile binding points at it. */
export function publishSessionState(runtimeId: string, state: ClientSessionState) {
  const current = $sessionStates.get()
  const prev = current[runtimeId] ?? null

  if (prev === state) {
    return
  }

  if (prev && evictable(runtimeId, state)) {
    handleTransition(prev, state, runtimeId)
    releaseSessionTranscript(runtimeId, state)

    return
  }

  $sessionStates.set({ ...current, [runtimeId]: state })
  handleTransition(prev, state, runtimeId)
}

/** Keep the cheap status projection for a cold session while releasing its
 * transcript. Unread completion is stored separately, so it survives too. */
export function releaseSessionTranscript(runtimeId: string, state?: ClientSessionState) {
  const current = $sessionStates.get()

  if (!(runtimeId in current)) {
    return
  }

  const retained = state ?? current[runtimeId]

  // Older persisted snapshots can contain an undefined state or omit the
  // messages field. Treat either shape as already cold instead of throwing
  // while memory pressure is being relieved.
  if (!retained) {
    return
  }

  const lightweight =
    Array.isArray(retained.messages) && retained.messages.length === 0 ? retained : { ...retained, messages: [] }

  $sessionStates.set({ ...current, [runtimeId]: lightweight })
}

export function dropSessionState(runtimeId: string) {
  // Disarm the watchdog — a dropped runtime must not fire a stale clear later.
  // Settle-grace entries are keyed by stored id and self-expire; leave them so
  // a just-finished session's row survives merge eviction even if its tile or
  // cached runtime is dropped in the meantime.
  clearWatchdog(runtimeId)
  clearSessionProviderWait(runtimeId)
  sessionScopeByRuntimeId.delete(runtimeId)

  const current = $sessionStates.get()
  setSessionStalled(current[runtimeId]?.storedSessionId, false)

  if (!(runtimeId in current)) {
    return
  }

  const { [runtimeId]: _dropped, ...rest } = current
  $sessionStates.set(rest)
}

/** Drop every cached session state — used on soft gateway-mode apply so the
 *  computed working / attention sets drain to empty alongside the session list.
 *  Also disarms every watchdog timer and drops all settle-grace entries: a
 *  wiped gateway's sessions must not fire stale clears or linger in the
 *  sidebar merge keep-set after the switch. */
export function clearAllSessionStates() {
  for (const timer of sessionWatchdogTimers.values()) {
    clearTimeout(timer)
  }

  sessionWatchdogTimers.clear()
  settledExpiry.clear()
  clearAllProviderWaits()
  sessionScopeByRuntimeId.clear()
  $stalledSessionIds.set([])
  $sessionStates.set({})
}

// Derived per-session status sets — pure projections of `$sessionStates` (which
// holds `busy`/`needsInput` per runtime), keeping the data flow one-directional:
// gateway event → cache → $sessionStates → computed views.
//
// Perf: `$sessionStates` is republished on EVERY message delta (tens/sec during
// a turn), but these sets only change on busy/needsInput edges. `stableArray`
// keeps the prior reference when membership is unchanged so `computed` skips the
// emit — otherwise the whole sidebar + every row re-renders per token.
// Published under every id the conversation answers to, not just its current
// tip: consumers hold whichever id they were created with, and compression
// rotates the tip out from under them (see lineageAliases).
//
// A conversation that has not been persisted yet has no stored id at all, and
// dropping it here is what left the FIRST turn of a new chat with no running
// indicator anywhere — no dot, no row arc — for as long as it took the backend
// to hand one back. Its runtime id is the right fallback because until a stored
// id exists the two are the same value (submit.ts: "an unpersisted
// conversation's queue key IS its runtime id"), so the row matches; once a
// session is persisted its runtime id is nobody's key and the fallback is inert.
const storedIds = (
  states: Record<string, ClientSessionState>,
  sessions: readonly SessionInfo[],
  pred: (s: ClientSessionState) => boolean
) => {
  const ids = new Set<string>()

  for (const [runtimeId, state] of Object.entries(states)) {
    if (!pred(state)) {
      continue
    }

    for (const alias of lineageAliases(state.storedSessionId ?? runtimeId, sessions)) {
      ids.add(alias)
    }
  }

  return [...ids]
}

export const sessionStatusKey = (profile: null | string | undefined, storedSessionId: string): string =>
  `${profile?.trim() || 'default'}\0${storedSessionId}`

const storedKeys = (
  states: Record<string, ClientSessionState>,
  sessions: readonly SessionInfo[],
  pred: (s: ClientSessionState) => boolean
) => {
  const keys = new Set<string>()

  const resolvedProfile = (state: ClientSessionState, runtimeId: string): null | string => {
    if (state.profile?.trim()) {
      return state.profile.trim()
    }

    const storedSessionId = state.storedSessionId ?? runtimeId

    const owners = new Set(
      sessions
        .filter(session => sessionMatchesStoredId(session, storedSessionId))
        .map(session => session.profile?.trim() || 'default')
    )

    if (owners.size === 1) {
      return [...owners][0]
    }

    return owners.size === 0 ? 'default' : null
  }

  for (const [runtimeId, state] of Object.entries(states)) {
    if (!pred(state)) {
      continue
    }

    const profile = resolvedProfile(state, runtimeId)

    if (!profile) {
      continue
    }

    const scopedSessions = sessions.filter(session => (session.profile?.trim() || 'default') === profile)

    for (const alias of lineageAliases(state.storedSessionId ?? runtimeId, scopedSessions)) {
      keys.add(sessionStatusKey(profile, alias))
    }
  }

  return [...keys]
}

let workingIds: readonly string[] = []
export const $workingSessionIds = computed(
  [$sessionStates, $sessions],
  (states, sessions) =>
    (workingIds = stableArray(
      workingIds,
      storedIds(states, sessions, s => s.busy)
    ))
)

let attentionIds: readonly string[] = []
export const $attentionSessionIds = computed(
  [$sessionStates, $sessions],
  (states, sessions) =>
    (attentionIds = stableArray(
      attentionIds,
      storedIds(states, sessions, s => s.needsInput)
    ))
)

let workingKeys: readonly string[] = []
export const $workingSessionKeys = computed(
  [$sessionStates, $sessions],
  (states, sessions) =>
    (workingKeys = stableArray(
      workingKeys,
      storedKeys(states, sessions, s => s.busy)
    ))
)

let attentionKeys: readonly string[] = []
export const $attentionSessionKeys = computed(
  [$sessionStates, $sessions],
  (states, sessions) =>
    (attentionKeys = stableArray(
      attentionKeys,
      storedKeys(states, sessions, s => s.needsInput)
    ))
)

// An open session nothing has ever been sent to — the ⌘T tab whose backend
// session exists but is unlisted, or a tile still waiting on its first send.
// `blankDraftTile`'s predicate, read as a status rather than as a slot to spend.
//
// The row's own `message_count` is the tiebreaker, and it is load-bearing: a
// session RESUMING also holds an empty message list for the moment between
// binding its runtime and loading its transcript, and calling that a draft
// would flash the wrong mark on a conversation with years of history in it.
let draftIds: readonly string[] = []
export const $draftSessionIds = computed([$sessionStates, $sessions], (states, sessions) => {
  const unsent = (state: ClientSessionState) => {
    if (state.busy || state.messages.length > 0) {
      return false
    }

    const storedId = state.storedSessionId

    // No stored id is the ⌘T tab that hasn't reached the backend yet: a draft
    // by definition, and no row to consult. Asking anyway would match a row on
    // an empty lineage root.
    if (!storedId) {
      return true
    }

    const row = sessions.find(session => sessionMatchesStoredId(session, storedId))

    return !row || row.message_count === 0
  }

  return (draftIds = stableArray(draftIds, storedIds(states, sessions, unsent)))
})

// ---------------------------------------------------------------------------
// Session tiles.
// ---------------------------------------------------------------------------

/** Edge a tile docks against main when it first joins the tree. Shared by
 *  session tiles and route (page) tiles. */
export type SplitDir = 'bottom' | 'left' | 'right' | 'top'

/** Where a tile lands on adoption: an edge split, or `center` = stack into
 *  the anchor's zone as a tab (a drop on the zone's tab strip). */
export type TileDock = 'center' | SplitDir

export interface SessionTile {
  /** Normalized profile that owns this stored session. */
  profile: string
  /** Stored session id — durable only together with `profile`. */
  storedSessionId: string
  /** Dock against `anchor` on adoption (default right; center = stack). */
  dir?: TileDock
  /** Pane to dock against (a drop's target zone) — default the workspace.
   *  Persisted so a restart re-docks in place; a stale id falls back to the
   *  workspace (findGroupOfPane misses → the move is skipped). */
  anchor?: string
  /** Center docks: stack BEFORE this pane id (`null`/omitted = append) — the
   *  strip divider's slot. Persisted, like `anchor`; a stale id appends. */
  before?: null | string
  /** Live runtime id once the tile's resume has bound one. */
  runtimeId?: string
  /** Resume failed terminally (shown in the tile; retryable). */
  error?: string
}

// The live list belongs to the window, not the currently routed gateway. v3
// flattens the former per-profile map and stamps every tile with explicit owner
// identity, allowing cloned profiles with the same stored id to coexist.
const TILES_KEY = 'hermes.desktop.sessionTiles.v3'
const PROFILE_TILES_KEY = 'hermes.desktop.sessionTiles.v2'
const LEGACY_TILES_KEY = 'hermes.desktop.sessionTiles.v1'
export const TILE_PANE_PREFIX = 'session-tile:'

export interface SessionTileIdentity {
  profile: string
  storedSessionId: string
}

export const sessionTileKey = (profile: null | string | undefined, storedSessionId: string): string =>
  encodeURIComponent(normalizeProfileKey(profile)) + ':' + encodeURIComponent(storedSessionId)

export const sessionTilePaneId = (profile: null | string | undefined, storedSessionId: string): string =>
  TILE_PANE_PREFIX + sessionTileKey(profile, storedSessionId)

const safeDecode = (value: string): string => {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

export function decodeSessionTileKey(key: string): SessionTileIdentity | null {
  const separator = key.indexOf(':')

  if (separator < 0) {
    return null
  }

  return {
    profile: normalizeProfileKey(safeDecode(key.slice(0, separator))),
    storedSessionId: safeDecode(key.slice(separator + 1))
  }
}

export function decodeSessionTilePaneId(paneId: string): (SessionTileIdentity & { legacy: boolean }) | null {
  if (!paneId.startsWith(TILE_PANE_PREFIX)) {
    return null
  }

  const raw = paneId.slice(TILE_PANE_PREFIX.length)
  const qualified = decodeSessionTileKey(raw)

  return qualified
    ? { ...qualified, legacy: false }
    : { profile: 'default', storedSessionId: safeDecode(raw), legacy: true }
}

export const sessionTileMatches = (
  tile: Pick<SessionTile, 'profile' | 'storedSessionId'>,
  storedSessionId: string,
  profile?: null | string
): boolean =>
  tile.storedSessionId === storedSessionId &&
  (profile == null || normalizeProfileKey(tile.profile) === normalizeProfileKey(profile))

/** Persisted placement. Runtime/error state is deliberately process-local. */
type StoredTile = Pick<SessionTile, 'anchor' | 'before' | 'dir' | 'profile' | 'storedSessionId'>

const normalizePaneReference = (value: unknown, profile: string): string | undefined => {
  if (typeof value !== 'string') {
    return undefined
  }

  const decoded = decodeSessionTilePaneId(value)

  return decoded?.legacy ? sessionTilePaneId(profile, decoded.storedSessionId) : value
}

const toStored = (tile: SessionTile): StoredTile => ({
  anchor: normalizePaneReference(tile.anchor, tile.profile),
  before: tile.before === null ? null : normalizePaneReference(tile.before, tile.profile),
  dir: tile.dir,
  profile: normalizeProfileKey(tile.profile),
  storedSessionId: tile.storedSessionId
})

function parseTileList(value: unknown, fallbackProfile: string): StoredTile[] {
  return Array.isArray(value)
    ? value
        .filter((tile): tile is Partial<SessionTile> & { storedSessionId: string } =>
          Boolean(tile && typeof (tile as Partial<SessionTile>).storedSessionId === 'string')
        )
        .map(raw => {
          const profile = normalizeProfileKey(raw.profile ?? fallbackProfile)

          return {
            anchor: normalizePaneReference(raw.anchor, profile),
            before: raw.before === null ? null : normalizePaneReference(raw.before, profile),
            dir: raw.dir,
            profile,
            storedSessionId: raw.storedSessionId
          }
        })
    : []
}

function loadWindowTiles(): StoredTile[] {
  const current = parseTileList(readJson<unknown>(TILES_KEY), 'default')
  const migrated: StoredTile[] = []
  const byProfile = readJson<unknown>(PROFILE_TILES_KEY)

  if (byProfile && typeof byProfile === 'object' && !Array.isArray(byProfile)) {
    for (const [profile, value] of Object.entries(byProfile as Record<string, unknown>)) {
      migrated.push(...parseTileList(value, profile))
    }
  }

  migrated.push(...parseTileList(readJson<unknown>(LEGACY_TILES_KEY), 'default'))
  const deduped = new Map<string, StoredTile>()

  for (const tile of [...current, ...migrated]) {
    const key = sessionTileKey(tile.profile, tile.storedSessionId)
    deduped.set(key, deduped.get(key) ?? tile)
  }

  const tiles = [...deduped.values()]

  if (migrated.length > 0) {
    writeJson(TILES_KEY, tiles)
  }

  writeJson(PROFILE_TILES_KEY, null)
  writeJson(LEGACY_TILES_KEY, null)

  return tiles
}

export const $sessionTiles = atom<SessionTile[]>(isSecondaryWindow() ? [] : loadWindowTiles())

function migrateLegacyTilePaneIds(node: LayoutNode, tiles: readonly SessionTile[]): LayoutNode {
  if (node.type === 'split') {
    return { ...node, children: node.children.map(child => migrateLegacyTilePaneIds(child, tiles)) }
  }

  const migrate = (paneId: string): string => {
    const decoded = decodeSessionTilePaneId(paneId)

    if (!decoded?.legacy) {
      return paneId
    }

    const owner =
      tiles.find(
        tile =>
          tile.storedSessionId === decoded.storedSessionId &&
          normalizeProfileKey(tile.profile) === normalizeProfileKey($activeGatewayProfile.get())
      ) ?? tiles.find(tile => tile.storedSessionId === decoded.storedSessionId)

    return owner ? sessionTilePaneId(owner.profile, owner.storedSessionId) : paneId
  }

  return { ...node, active: migrate(node.active), panes: node.panes.map(migrate) }
}

const restoredTree = $layoutTree.get()

if (!isSecondaryWindow() && restoredTree) {
  const migratedTree = migrateLegacyTilePaneIds(restoredTree, $sessionTiles.get())

  if (JSON.stringify(migratedTree) !== JSON.stringify(restoredTree)) {
    $layoutTree.set(migratedTree)
  }
}

function saveTiles(tiles: SessionTile[]) {
  $sessionTiles.set(tiles)

  if (!isSecondaryWindow()) {
    const stored = tiles.map(toStored)
    writeJson(TILES_KEY, stored.length > 0 ? stored : null)
  }
}

export function findSessionTile(storedSessionId: string, profile?: null | string): SessionTile | undefined {
  const tiles = $sessionTiles.get()

  if (profile != null) {
    return tiles.find(tile => sessionTileMatches(tile, storedSessionId, profile))
  }

  const activeProfile = normalizeProfileKey($activeGatewayProfile.get())

  return (
    tiles.find(tile => sessionTileMatches(tile, storedSessionId, activeProfile)) ??
    tiles.find(tile => tile.storedSessionId === storedSessionId)
  )
}

export function patchSessionTile(storedSessionId: string, patch: Partial<SessionTile>, profile?: null | string) {
  const target = findSessionTile(storedSessionId, profile)

  if (!target) {
    return
  }

  saveTiles(
    $sessionTiles
      .get()
      .map(tile => (sessionTileMatches(tile, target.storedSessionId, target.profile) ? { ...tile, ...patch } : tile))
  )
}

/** Drop live runtime bindings so every tile re-resumes — used on gateway
 *  reconnect, where a respawned backend re-mints (recycles) runtime ids.
 *  Also invalidates the wiring cache's stored→runtime map: clearing only the
 *  tile atoms left `resumeTile`'s warm path free to re-bind the same dead
 *  runtime id from the cache, so post-wake tiles repainted empty and never
 *  actually re-resumed. */
export function resetTileRuntimeBindings() {
  sessionTileDelegate()?.invalidateRuntimeBindings?.()

  const tiles = $sessionTiles.get()

  if (tiles.some(t => t.runtimeId)) {
    $sessionTiles.set(tiles.map(toStored))
  }
}

/** Unbind ONE reclaimed runtime from whichever tile holds it — the targeted
 *  sibling of resetTileRuntimeBindings. The reconnect-time reset can't cover a
 *  backend reclaim: the WS re-dials immediately, but the orphan reaper fires a
 *  grace window LATER, so the reclaim lands after every reconnect-path unbind
 *  already ran. Without this, the tile keeps pointing at the dead runtime whose
 *  state `session.reclaimed` just dropped — an empty transcript under live
 *  chrome — and SessionTilePane's resume effect (gated on `!runtimeId`) never
 *  re-resumes. Clearing the binding re-arms that effect, which rebinds a fresh
 *  runtime from the stored row. The pane itself stays: the stored session is
 *  intact, only its live runtime was reclaimed. */
export function unbindTileRuntime(runtimeId: string) {
  const tiles = $sessionTiles.get()

  if (tiles.some(t => t.runtimeId === runtimeId)) {
    $sessionTiles.set(tiles.map(t => (t.runtimeId === runtimeId ? { ...t, runtimeId: undefined } : t)))
  }
}

// ---------------------------------------------------------------------------
// Delegate — the wiring layer (which owns the gateway + session cache) plugs
// its actions in; tile UI calls through here. Same inversion as the tree
// store's pane closers.
// ---------------------------------------------------------------------------

export interface SessionTileDelegate {
  /** Archive a stored session (the sidebar's archive, incl. tile cleanup). */
  archiveSession(storedSessionId: string, profile: string): Promise<void>
  /** Branch a stored session into a new chat (the sidebar's branch). */
  branchSession(storedSessionId: string, profile: string): Promise<void>
  /** Delete a stored session (the sidebar's delete, incl. tile cleanup). */
  deleteSession(storedSessionId: string, profile: string): Promise<void>
  /** Run a slash command against a tile's session (app-level effects — e.g.
   *  branch/handoff — act on the main surface, as they should). */
  executeSlash(rawCommand: string, sessionId: string, profile: string): Promise<void>
  /** Interrupt a tile's running turn. */
  interruptSession(runtimeId: string, profile: string): Promise<void>
  /** Discard only this renderer's runtime/cache binding and re-arm the tile's
   *  authoritative resume. The backend session and running turn continue. */
  rehydrateTile(storedSessionId: string, profile: string): void
  /** Drop the wiring cache's stored→runtime bindings. Called on gateway
   *  reconnect: a respawned backend re-mints runtime ids, so every binding
   *  recorded before the reconnect is suspect — without this, `resumeTile`'s
   *  warm path re-binds tiles to dead runtime ids (the sleep/wake "empty
   *  right pane" bug). Bindings re-record from live post-reconnect events. */
  invalidateRuntimeBindings?(): void
  /** Bind a live runtime id for a stored session (resume without touching
   *  the main view). Returns the runtime id, or throws. */
  resumeTile(storedSessionId: string, profile: string): Promise<string>
  /** Submit a prompt to a tile's live session. */
  submitToSession(runtimeId: string, text: string, profile: string): Promise<void>
  /** THE session-state write path — routes through the wiring cache so the
   *  cache, the primary view (when active), and every tile mirror agree. */
  updateSession(runtimeId: string, updater: (state: ClientSessionState) => ClientSessionState): ClientSessionState
}

let delegate: SessionTileDelegate | null = null

export function setSessionTileDelegate(next: SessionTileDelegate) {
  delegate = next
}

export function sessionTileDelegate(): SessionTileDelegate | null {
  return delegate
}

/** Reorder tiles to match layout-tree encounter order (stored ids in the order
 *  their `session-tile:` panes are walked). Restore replays the array through
 *  sequential adoption (each center tile APPENDS after the ones before it), so
 *  array order IS strip order — no `before` stamping needed; a stale `before`
 *  naming an absent pane falls back to append anyway (see insertAtGroup). Tiles
 *  not yet adopted sort after placed ones, stably. Returns `null` when nothing
 *  moves so callers can skip a needless persist. */
export function orderTilesByTree<T extends SessionTileIdentity>(
  tree: LayoutNode | null,
  tiles: readonly T[]
): null | T[] {
  if (!tree || tiles.length < 2) {
    return null
  }

  const order: string[] = []

  const walk = (node: LayoutNode) => {
    if (node.type === 'group') {
      for (const id of node.panes) {
        if (id.startsWith(TILE_PANE_PREFIX)) {
          const decoded = decodeSessionTilePaneId(id)

          if (decoded) {
            const identity = decoded.legacy
              ? (tiles.find(
                  tile =>
                    tile.storedSessionId === decoded.storedSessionId &&
                    normalizeProfileKey(tile.profile) === normalizeProfileKey($activeGatewayProfile.get())
                ) ?? tiles.find(tile => tile.storedSessionId === decoded.storedSessionId))
              : decoded

            if (identity) {
              order.push(sessionTileKey(identity.profile, identity.storedSessionId))
            }
          }
        }
      }

      return
    }

    node.children.forEach(walk)
  }

  walk(tree)

  const rank = new Map(order.map((id, i) => [id, i]))

  const next = [...tiles].sort(
    (a, b) =>
      (rank.get(sessionTileKey(a.profile, a.storedSessionId)) ?? Infinity) -
      (rank.get(sessionTileKey(b.profile, b.storedSessionId)) ?? Infinity)
  )

  return next.some((t, i) => t !== tiles[i]) ? next : null
}

function syncTileStripOrder() {
  const next = orderTilesByTree($layoutTree.get(), $sessionTiles.get())

  if (next) {
    saveTiles(next)
  }
}

/** Open a tile for a stored session, or MOVE an existing one to the new dock
 *  (`dir`; `center` = stack into the anchor's zone, `before` = strip slot). The
 *  move path is what lets a tile's own TAB be dragged like a sidebar row — drop
 *  it on a zone/edge/strip and the tile goes there (drop-on-a-composer links
 *  instead, handled by the drag resolver). The session LOADED IN MAIN never
 *  opens as a tile (same transcript twice, fighting one runtime — silly).
 *
 *  An unanchored open (⌘T, ⌘⇧T on a tile that predates anchors) docks into the
 *  FOCUSED chat zone — the same zone ⌘1…⌘9 and ⌘W act on — so a new tab lands
 *  in the strip the user is looking at, not always main's. */
export function openSessionTile(
  storedSessionId: string,
  dir: TileDock = 'right',
  anchor?: string,
  before?: null | string,
  profile: string = $activeGatewayProfile.get()
) {
  const owner = normalizeProfileKey(profile)
  const tiles = $sessionTiles.get()

  // Opening a session in a tab/tile is reading it, including profile-qualified
  // tiles whose stored id may also exist under another profile.
  markSessionRead(storedSessionId)
  ackStoredSessionId(storedSessionId)

  if (
    storedSessionId === $selectedStoredSessionId.get() &&
    owner === normalizeProfileKey($activeGatewayProfile.get())
  ) {
    return
  }

  const dock = anchor ?? focusedSessionTabAnchor() ?? undefined
  const existing = tiles.find(tile => sessionTileMatches(tile, storedSessionId, owner))

  if (!existing) {
    saveTiles([...tiles, { anchor: dock, before, dir, profile: owner, storedSessionId }])

    return
  }

  const tree = $layoutTree.get()
  const target = tree ? findGroupOfPane(tree, dock ?? 'workspace')?.id : null

  if (target) {
    moveTreePane(sessionTilePaneId(owner, storedSessionId), { before: before ?? null, groupId: target, pos: dir })
    patchSessionTile(storedSessionId, { anchor: dock, before: before ?? undefined, dir }, owner)
    syncTileStripOrder()
  }
}

/** ⌘W on the MAIN tab: the next session tab to shift into main. Prefer the
 *  workspace's own strip (after it first, then wrapping before it); if that
 *  strip has none, fall back to the first session tile elsewhere in the tree.
 *  The fallback prevents a blank workspace tab becoming an uncloseable visual
 *  trap merely because the other sessions were split into another zone. */
export function nextSessionTileForWorkspace(): null | SessionTile {
  const tree = $layoutTree.get()
  const group = tree ? findGroupOfPane(tree, 'workspace') : null

  if (!tree || !group) {
    return null
  }

  const tiles = $sessionTiles.get()
  const idx = group.panes.indexOf('workspace')
  const local = [...group.panes.slice(idx + 1), ...group.panes.slice(0, idx).reverse()]
  const elsewhere = allPaneIds(tree).filter(paneId => !group.panes.includes(paneId))

  for (const paneId of [...local, ...elsewhere]) {
    const decoded = decodeSessionTilePaneId(paneId)

    if (!decoded) {
      continue
    }

    const tile = decoded.legacy
      ? findSessionTile(decoded.storedSessionId)
      : findSessionTile(decoded.storedSessionId, decoded.profile)

    if (tile) {
      return tile
    }
  }

  // Nothing stacked WITH main — but a session tile in another zone can still
  // shift in. Without this, closing main in a side-by-side layout skipped
  // promotion entirely and dropped to a fresh "New session" draft, which read
  // as "closing a pane gave me a new session" (#88924). Promoting the tile
  // also collapses its zone, so Close is how a multi-pane layout shrinks.
  for (const tile of tiles) {
    if (tree && findGroupOfPane(tree, `${TILE_PANE_PREFIX}${tile.storedSessionId}`)) {
      return tile
    }
  }

  return null
}

export function openTileNeedsHydration(
  tile: Pick<SessionTile, 'runtimeId' | 'storedSessionId'>,
  state: ClientSessionState | undefined,
  stored: SessionInfo | undefined
): boolean {
  if (!tile.runtimeId || !state) {
    return true
  }

  if (state.messages.length > 0) {
    return false
  }

  return Boolean(stored?.is_active) || (stored?.message_count ?? 0) > 0
}

/** If a session is already ON SCREEN — an open tile OR the one loaded in main —
 *  front its tab (and focus its zone) and report WHICH. A sidebar click on an
 *  already-open chat JUMPS to its tab instead of reloading it; `null` means the
 *  caller must load it into main. Covers the two dead clicks: an open tile, and
 *  the main session while focus sits on a tile (route unchanged → no reload).
 *  Callers that own the router need the `'main'` vs `'tile'` distinction: a
 *  `'main'` hit only reaches the screen if the workspace pane is actually
 *  showing the chat, whereas a tile renders in its own pane regardless. */
export function focusOpenSession(storedSessionId: string, profile?: string): 'main' | 'tile' | null {
  const tile = findSessionTile(storedSessionId, profile)

  if (tile) {
    const state = tile.runtimeId ? $sessionStates.get()[tile.runtimeId] : undefined
    const stored = $sessions
      .get()
      .find(
        session =>
          sessionMatchesStoredId(session, storedSessionId) &&
          normalizeProfileKey(session.profile) === normalizeProfileKey(tile.profile)
      )

    // An already-open tab is normally focus-only. If its binding is missing or
    // its idle projection is empty despite stored history, that shortcut merely
    // reveals a blank pane. Unbind it so SessionTile's existing resume effect
    // performs the profile-safe REST + gateway hydrate. Busy first turns and
    // genuinely empty stored sessions remain instant focus-only hits.
    if (openTileNeedsHydration(tile, state, stored)) {
      patchSessionTile(storedSessionId, { error: undefined, runtimeId: undefined }, tile.profile)
    }

    const paneId = sessionTilePaneId(tile.profile, storedSessionId)
    revealTreePane(paneId) // un-dismiss + adopt + front in its group
    const tree = $layoutTree.get()
    const group = tree ? findGroupOfPane(tree, paneId) : null

    if (group) {
      noteActiveTreeGroup(group.id)
    }

    return 'tile'
  }

  // Already the main session: front the workspace tab and drop tile focus so
  // the readouts + sidebar highlight come home (a no-op when main is focused).
  if (
    storedSessionId === $selectedStoredSessionId.get() &&
    (profile == null || normalizeProfileKey(profile) === normalizeProfileKey($activeGatewayProfile.get()))
  ) {
    revealTreePane('workspace')
    noteActiveTreeGroup(null)

    return 'main'
  }

  return null
}

/** Does a sidebar click still need to navigate after `focusOpenSession`? A miss
 *  always does. A `'main'` hit does too while the workspace pane is showing a
 *  full page (artifacts, skills, …): fronting the workspace tab doesn't put the
 *  chat back on screen — only a route change back to the session does. A tile
 *  hit never does; its pane renders the chat regardless of the route. */
export function focusedSessionNeedsRoute(focused: 'main' | 'tile' | null, workspaceIsPage: boolean): boolean {
  return !focused || (focused === 'main' && workspaceIsPage)
}

/** The open tab that's still an empty "New session" draft, if there is one.
 *  That tab is the one the user would have typed into, so an open-from-nowhere
 *  spends it instead of stacking a second blank tab beside it. Most recent
 *  wins; a tile whose runtime hasn't bound (or whose state hasn't published) is
 *  unknown rather than empty, so it's left alone. */
export function blankDraftTile(
  tiles: readonly SessionTile[],
  states: Record<string, ClientSessionState>
): null | SessionTile {
  return (
    tiles.findLast(({ runtimeId }) => {
      const state = runtimeId ? states[runtimeId] : undefined

      return Boolean(state && !state.busy && state.messages.length === 0)
    }) ?? null
  )
}

/** Hand an open blank draft tab over to `storedSessionId`, keeping its slot.
 *  False when there's no such tab, so the caller can fall back. The spent draft
 *  is DISCARDED rather than closed: it never held a conversation, so ⌘⇧T
 *  resurrecting it would just restore an empty tab. */
export function reuseBlankDraftTile(storedSessionId: string, profile?: string): boolean {
  const tile = blankDraftTile($sessionTiles.get(), $sessionStates.get())

  if (!tile || sessionTileMatches(tile, storedSessionId, profile)) {
    return false
  }

  discardSessionTile(tile.storedSessionId, tile.profile)
  openSessionTile(storedSessionId, tile.dir, tile.anchor, tile.before, profile)
  revealTreePane(sessionTilePaneId(profile, storedSessionId))

  return true
}

// Closed-tab stack for ⌘⇧T reopen (window-local). Identity remains qualified,
// so switching gateways cannot resurrect or close a cloned profile sibling.
const closedTiles: SessionTile[] = []

export function closeSessionTile(storedSessionId: string, profile?: string) {
  const tile = findSessionTile(storedSessionId, profile)

  if (!tile) {
    return
  }

  closedTiles.push(toStored(tile))
  saveTiles($sessionTiles.get().filter(candidate => !sessionTileMatches(candidate, tile.storedSessionId, tile.profile)))

  const runtimeId = tile?.runtimeId
  const state = runtimeId ? $sessionStates.get()[runtimeId] : undefined

  if (runtimeId && state && evictable(runtimeId, state)) {
    dropSessionState(runtimeId)
  }
}

/** Drop a dead tile without adding it to the reopen stack. */
export function discardSessionTile(storedSessionId: string, profile?: string) {
  const tile = findSessionTile(storedSessionId, profile)

  if (!tile) {
    return
  }

  if (tile?.runtimeId) {
    dropSessionState(tile.runtimeId)
  }

  saveTiles($sessionTiles.get().filter(candidate => !sessionTileMatches(candidate, tile.storedSessionId, tile.profile)))
}

/** ⌘⇧T — reopen the most recently closed tab where it was, then focus it. */
export function reopenLastClosedTile(): void {
  for (let tile = closedTiles.pop(); tile; tile = closedTiles.pop()) {
    if (
      tile.storedSessionId === $selectedStoredSessionId.get() &&
      normalizeProfileKey(tile.profile) === normalizeProfileKey($activeGatewayProfile.get())
    ) {
      continue
    }

    if (!findSessionTile(tile.storedSessionId, tile.profile)) {
      openSessionTile(tile.storedSessionId, tile.dir, tile.anchor, tile.before, tile.profile)
      focusOpenSession(tile.storedSessionId, tile.profile)

      return
    }
  }
}

// ---------------------------------------------------------------------------
// The FOCUSED session — one derivation, not another hand-maintained
// "$activeSession" sibling. The layout's interaction tracker ($activeTreeGroup:
// last click/focus, the same source ⌘W uses) resolves to a zone; its active
// pane names the session: a `session-tile:<storedId>` pane IS that session,
// anything else falls back to the route-driven primary. Chrome that should
// follow the user between tiles (titlebar session title, statusbar context /
// timer / model) reads these instead of the primary-only atoms.
// ---------------------------------------------------------------------------

export const $focusedSessionTile = computed([$activeTreeGroup, $layoutTree, $sessionTiles], (groupId, tree, tiles) => {
  const active = groupId && tree ? findGroup(tree, groupId)?.active : undefined
  const identity = active ? decodeSessionTilePaneId(active) : null

  if (!identity) {
    return null
  }

  return identity.legacy
    ? (tiles.find(tile => tile.storedSessionId === identity.storedSessionId) ?? null)
    : (tiles.find(tile => sessionTileMatches(tile, identity.storedSessionId, identity.profile)) ?? null)
})

/** Stored id of the focused session. */
export const $focusedStoredSessionId = computed(
  [$focusedSessionTile, $selectedStoredSessionId],
  (tile, selected) => tile?.storedSessionId ?? selected
)

/** Owning profile of a focused tile, else the active primary profile. */
export const $focusedSessionProfile = computed([$focusedSessionTile, $activeGatewayProfile], (tile, profile) =>
  normalizeProfileKey(tile?.profile ?? profile)
)

/** Live runtime id of the focused session. */
export const $focusedRuntimeId = computed(
  [$focusedSessionTile, $activeSessionId],
  (tile, primaryRuntime) => tile?.runtimeId ?? primaryRuntime
)

/** The focused session's state slice (undefined while unresolved/unbound). */
export const $focusedSessionState = computed([$focusedRuntimeId, $sessionStates], (runtimeId, states) =>
  runtimeId ? states[runtimeId] : undefined
)

/** A PRIMARY navigation (sidebar resume, route change, new chat) homes focus to
 *  the workspace — UNLESS the selected id is already an open TILE, where
 *  `focusOpenSession` owns the move and homing would yank every stacked tile
 *  behind the workspace (A+B "disappear" when switching to C). */
export const selectionHomesToWorkspace = (
  selected: null | string,
  tiles: readonly SessionTile[],
  profile: string = $activeGatewayProfile.get()
): boolean => !(selected && tiles.some(tile => sessionTileMatches(tile, selected, profile)))

// Bringing a finished session to the front clears its green dot. Keyed on the
// FOCUSED session, not the selected one: a tile is never $selectedStoredSessionId,
// and a tile tab click goes through activateTreePane rather than focusOpenSession,
// so this is the one hook that catches every way a tile reaches the front.
// Clears the whole conversation family (markSessionRead) AND acks the
// persisted watermark/marker (ackStoredSessionId) so the next list refresh
// doesn't repaint the dot the user just cleared by looking at it.
$focusedStoredSessionId.listen(focused => {
  if (focused) {
    markSessionRead(focused)
    ackStoredSessionId(focused)
  }
})

// Cold-start restore is the one selection change that is NOT a navigation: the
// route already pointed at the primary session before the window loaded, and
// homing on it would front the workspace tab over the PERSISTED active tab —
// then persist that clobber, so the tab you reloaded on never comes back
// (⌘R always landing on main). use-route-resume arms this one-shot right
// before dispatching the boot resume; the very next selection change skips
// homing and the restored layout tree keeps its say.
let selectionRestoreInFlight = false

export function markSelectionRestore() {
  selectionRestoreInFlight = true
}

// Homing also FRONTS the workspace tab: the resumed chat loads in the workspace
// pane, so a zone parked on a tile tab must switch back or the click looks dead.
$selectedStoredSessionId.listen(selected => {
  const restoring = selectionRestoreInFlight
  selectionRestoreInFlight = false

  if (restoring || !selectionHomesToWorkspace(selected, $sessionTiles.get())) {
    return
  }

  noteActiveTreeGroup(null)
  revealTreePane('workspace')
})

// Dev hook for automation (mirrors __HERMES_LAYOUT_TREE__).
if ((import.meta.env.DEV || import.meta.env.VITE_PERF_PROBE === '1') && typeof window !== 'undefined') {
  ;(window as unknown as Record<string, unknown>).__HERMES_SESSION_TILES__ = {
    close: closeSessionTile,
    drop: dropSessionState,
    open: openSessionTile,
    patch: patchSessionTile,
    publish: publishSessionState,
    /** Seed the recents list — models a populated sessions DB in perf runs. */
    seedSessions: (rows: SessionInfo[]) => setSessions(rows),
    sessions: () => $sessions.get(),
    states: () => $sessionStates.get(),
    tiles: () => $sessionTiles.get(),
    /** THE real gateway write path (wiring cache + journal + publish + view
     *  sync), unlike `publish` which only touches the store. Perf scenarios
     *  must drive this or they under-model streaming cost. */
    update: (runtimeId: string, updater: (state: ClientSessionState) => ClientSessionState) =>
      sessionTileDelegate()?.updateSession(runtimeId, updater)
  }
}
