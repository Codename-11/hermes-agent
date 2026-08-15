import type { ClientSessionState } from '@/app/types'
import { chatMessageText, sealOpenToolParts } from '@/lib/chat-messages'
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

export type LiveSessionStatusBaseline = Map<string, ClientSessionState | undefined>
type SessionStateUpdater = (state: ClientSessionState) => ClientSessionState
type SessionStateReconciler = (
  runtimeSessionId: string,
  updater: SessionStateUpdater,
  storedSessionId?: string | null
) => ClientSessionState

let reconcileSessionState: SessionStateReconciler | null = null
const liveRuntimeIdsByProfile = new Map<string, Set<string>>()

export function setLiveSessionStateReconciler(reconciler: SessionStateReconciler): () => void {
  reconcileSessionState = reconciler

  return () => {
    if (reconcileSessionState === reconciler) {
      reconcileSessionState = null
    }
  }
}

export function captureLiveSessionStatusBaseline(): LiveSessionStatusBaseline {
  return new Map(Object.entries($sessionStates.get()))
}

function applySessionState(runtimeSessionId: string, updater: SessionStateUpdater, storedSessionId?: string | null) {
  if (reconcileSessionState) {
    return reconcileSessionState(runtimeSessionId, updater, storedSessionId)
  }

  const previous = $sessionStates.get()[runtimeSessionId] ?? createClientSessionState(storedSessionId)
  const next = updater(previous)
  publishSessionState(runtimeSessionId, next)

  return next
}

function requestStillOwnsRuntime(runtimeSessionId: string, baseline?: LiveSessionStatusBaseline): boolean {
  return !baseline || baseline.get(runtimeSessionId) === $sessionStates.get()[runtimeSessionId]
}

function finalizePendingMessages(state: ClientSessionState) {
  const settled = state.messages
    .filter(message => !((message.pending || message.id === state.streamId) && !chatMessageText(message).trim()))
    .map(message => (message.pending || message.id === state.streamId ? { ...message, pending: false } : message))

  return sealOpenToolParts(settled)
}

function settleRuntime(state: ClientSessionState): ClientSessionState {
  return {
    ...state,
    adoptedRunningTurn: false,
    awaitingResponse: false,
    busy: false,
    interimBoundaryPending: false,
    messages: finalizePendingMessages(state),
    needsInput: false,
    pendingBranchGroup: null,
    streamId: null,
    turnStartedAt: null
  }
}

/** Restore renderer liveness from one gateway's authoritative in-memory
 * session registry. A captured baseline fences snapshots that resolve after a
 * newer stream event changed the same runtime. */
export function rehydrateLiveSessionStatuses(
  response: LiveSessionStatusResponse,
  nowMs = Date.now(),
  profileKey = 'default',
  authoritativeReconnect = false,
  baseline?: LiveSessionStatusBaseline
): void {
  const seen = new Set<string>()
  const normalizedProfile = profileKey.trim() || 'default'

  for (const session of response.sessions ?? []) {
    const runtimeSessionId = session.id?.trim()
    const storedSessionId = session.session_key?.trim()
    const needsInput = session.status === 'waiting'
    const working = session.status === 'working' || needsInput

    if (!runtimeSessionId || !storedSessionId) {
      continue
    }

    seen.add(runtimeSessionId)

    if (!requestStillOwnsRuntime(runtimeSessionId, baseline)) {
      continue
    }

    applySessionState(
      runtimeSessionId,
      state => {
        // Only a locally submitted, not-yet-observed turn outranks a brief idle
        // snapshot. An adopted turn must accept authoritative settlement.
        const preserveLocalSubmit = state.awaitingResponse && !state.sawAssistantPayload && !state.adoptedRunningTurn

        if (!working && !preserveLocalSubmit) {
          return { ...settleRuntime(state), profile: normalizedProfile, storedSessionId }
        }

        return {
          ...state,
          busy: working || preserveLocalSubmit,
          needsInput,
          profile: normalizedProfile,
          storedSessionId,
          turnStartedAt: working ? (state.turnStartedAt ?? Date.now()) : state.turnStartedAt
        }
      },
      storedSessionId
    )

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

  if (authoritativeReconnect) {
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

  for (const runtimeSessionId of previouslyLive) {
    if (seen.has(runtimeSessionId)) {
      continue
    }

    if (!requestStillOwnsRuntime(runtimeSessionId, baseline)) {
      // Preserve provenance so the next fresh snapshot can still reap it. A
      // stale response must not both skip settlement and forget the runtime.
      seen.add(runtimeSessionId)

      continue
    }

    const existing = $sessionStates.get()[runtimeSessionId]

    if (existing?.busy || existing?.needsInput || existing?.awaitingResponse || existing?.streamId) {
      applySessionState(runtimeSessionId, settleRuntime, existing.storedSessionId)
    }
  }

  liveRuntimeIdsByProfile.set(profileKey, seen)
}

export function resetLiveRuntimeTracking(): void {
  liveRuntimeIdsByProfile.clear()
}
