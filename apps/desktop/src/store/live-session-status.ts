import { createClientSessionState } from '@/lib/chat-runtime'

import { $sessions, sessionMatchesStoredId } from './session'
import { $sessionStates, publishSessionState, SESSION_WATCHDOG_TIMEOUT_MS, setSessionStalled } from './session-states'

export interface LiveSessionStatusItem {
  id?: string
  last_active?: number
  session_key?: string
  status?: 'idle' | 'starting' | 'waiting' | 'working'
}

export interface LiveSessionStatusResponse {
  sessions?: LiveSessionStatusItem[]
}

// Runtime ids this poll has seen live, per gateway profile. A profile only
// ever reaps what its OWN snapshot previously reported: background profiles are
// served by different gateways and never appear in this profile's active_list.
const liveRuntimeIdsByProfile = new Map<string, Set<string>>()

/** Restore renderer liveness from one gateway's authoritative in-memory
 * session registry. Absence from a profile's next snapshot is a terminal edge:
 * the runtime ended while its WebSocket events were unavailable. */
export function rehydrateLiveSessionStatuses(
  response: LiveSessionStatusResponse,
  nowMs = Date.now(),
  profileKey = 'default',
  authoritativeReconnect = false
): void {
  const seen = new Set<string>()

  for (const session of response.sessions ?? []) {
    const runtimeSessionId = session.id?.trim()
    const storedSessionId = session.session_key?.trim()
    const needsInput = session.status === 'waiting'
    const working = session.status === 'working' || needsInput

    if (!runtimeSessionId || !storedSessionId) {
      continue
    }

    seen.add(runtimeSessionId)
    const existing = $sessionStates.get()[runtimeSessionId]
    // A locally submitted turn is newer than the backend's brief pre-start idle
    // snapshot. Do not darken it before the first assistant payload arrives.
    const busy = working || Boolean(existing?.awaitingResponse && !existing.sawAssistantPayload)

    if (
      !existing ||
      existing.storedSessionId !== storedSessionId ||
      existing.busy !== busy ||
      existing.needsInput !== needsInput
    ) {
      publishSessionState(runtimeSessionId, {
        ...(existing ?? createClientSessionState(storedSessionId)),
        busy,
        needsInput,
        storedSessionId
      })
    }

    if (!working) {
      setSessionStalled(storedSessionId, false)

      continue
    }

    const lastActiveMs = Number(session.last_active) * 1000

    const isQuiet =
      session.status === 'working' &&
      Number.isFinite(lastActiveMs) &&
      lastActiveMs > 0 &&
      nowMs - lastActiveMs >= SESSION_WATCHDOG_TIMEOUT_MS

    setSessionStalled(storedSessionId, isQuiet)
  }

  const previouslyLive = new Set(liveRuntimeIdsByProfile.get(profileKey) ?? [])

  // A reconnect may be the first active_list snapshot for this profile. Include
  // stream-seeded busy states whose durable rows belong to this profile, so a
  // turn that finished while disconnected cannot remain spinning forever.
  if (authoritativeReconnect) {
    const normalizedProfile = profileKey.trim() || 'default'

    for (const [runtimeSessionId, state] of Object.entries($sessionStates.get())) {
      if ((!state.busy && !state.needsInput) || !state.storedSessionId) {
        continue
      }

      const row = $sessions
        .get()
        .find(
          session =>
            sessionMatchesStoredId(session, state.storedSessionId!) &&
            ((session.profile ?? '').trim() || 'default') === normalizedProfile
        )

      if (row) {
        previouslyLive.add(runtimeSessionId)
      }
    }
  }

  if (previouslyLive.size > 0) {
    for (const runtimeSessionId of previouslyLive) {
      if (seen.has(runtimeSessionId)) {
        continue
      }

      const existing = $sessionStates.get()[runtimeSessionId]

      if (existing?.busy || existing?.needsInput) {
        publishSessionState(runtimeSessionId, {
          ...existing,
          awaitingResponse: false,
          busy: false,
          needsInput: false,
          streamId: null,
          turnStartedAt: null
        })
      }
    }
  }

  liveRuntimeIdsByProfile.set(profileKey, seen)
}

/** Forget profile snapshot provenance after a full gateway wipe. */
export function resetLiveRuntimeTracking(): void {
  liveRuntimeIdsByProfile.clear()
}
