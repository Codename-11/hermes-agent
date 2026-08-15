import { useEffect } from 'react'

import { getLatestSessionMessages, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS } from '@/hermes'
import { toChatMessages } from '@/lib/chat-messages'
import { ensureGatewayProfile, normalizeProfileKey } from '@/store/profile'
import {
  dropSessionState,
  findSessionTile,
  patchSessionTile,
  publishSessionState,
  setSessionTileDelegate
} from '@/store/session-states'
import type { SessionResumeResponse } from '@/types/hermes'

import type { usePromptActions } from '../../session/hooks/use-prompt-actions'
import { markSessionRecentlyInterrupted, withSessionNotFoundResume } from '../../session/hooks/use-prompt-actions/utils'
import { appendLiveSessionProjection, resolveSessionProfile } from '../../session/hooks/use-session-actions/utils'
import type { useSessionStateCache } from '../../session/hooks/use-session-state-cache'
import type { GatewayRequester } from '../types'

type SessionStateCache = ReturnType<typeof useSessionStateCache>

interface SessionTileDelegateParams {
  archiveSession: (storedSessionId: string, profile?: string) => Promise<unknown>
  branchStoredSession: (storedSessionId: string, profile?: string) => Promise<unknown>
  executeSlashCommand: ReturnType<typeof usePromptActions>['executeSlashCommand']
  removeSession: (storedSessionId: string, profile?: string) => Promise<unknown>
  requestGateway: GatewayRequester
  runtimeIdByStoredSessionIdRef: SessionStateCache['runtimeIdByStoredSessionIdRef']
  sessionStateByRuntimeIdRef: SessionStateCache['sessionStateByRuntimeIdRef']
  updateSessionState: SessionStateCache['updateSessionState']
}

/**
 * Publishes the session-tile delegate: resume / submit / interrupt / slash for
 * tiled sessions WITHOUT touching the primary view ($activeSessionId /
 * $messages stay the main thread's). Resume reuses a live runtime binding when
 * one exists (incl. the main thread's own session); a cold tile binds +
 * hydrates the cache, which publishSessionState mirrors to the tile.
 */
export function useSessionTileDelegate({
  archiveSession,
  branchStoredSession,
  executeSlashCommand,
  removeSession,
  requestGateway,
  runtimeIdByStoredSessionIdRef,
  sessionStateByRuntimeIdRef,
  updateSessionState
}: SessionTileDelegateParams): void {
  useEffect(() => {
    // A tile's runtime binding can die the same way the foreground's does
    // (sleep/wake, backend restart). The cache maps stored -> runtime, so walk
    // it backwards to find the durable id this runtime belongs to.
    const storedSessionIdForRuntime = (runtimeId: string): null | string => {
      const cached = sessionStateByRuntimeIdRef.current.get(runtimeId)?.storedSessionId

      if (cached) {
        return cached
      }

      for (const [storedId, mapped] of runtimeIdByStoredSessionIdRef.current) {
        if (mapped === runtimeId) {
          return storedId
        }
      }

      return null
    }

    // Repoint the stored -> runtime mapping at the recovered id so subsequent
    // tile actions use the live binding instead of re-recovering every call.
    const rebindTileRuntime = (deadRuntimeId: string) => (recoveredId: string) => {
      const storedId = storedSessionIdForRuntime(deadRuntimeId)

      if (storedId) {
        runtimeIdByStoredSessionIdRef.current.set(storedId, recoveredId)
      }
    }

    setSessionTileDelegate({
      archiveSession: async (storedSessionId, profile) => {
        await ensureGatewayProfile(profile)
        await archiveSession(storedSessionId, profile)
      },
      branchSession: async (storedSessionId, profile) => {
        await ensureGatewayProfile(profile)
        await branchStoredSession(storedSessionId, profile)
      },
      deleteSession: async (storedSessionId, profile) => {
        await ensureGatewayProfile(profile)
        await removeSession(storedSessionId, profile)
      },
      executeSlash: async (rawCommand, sessionId, profile) => {
        await ensureGatewayProfile(profile)
        await executeSlashCommand(rawCommand, { sessionId })
      },
      interruptSession: async (runtimeId, profile) => {
        await ensureGatewayProfile(profile)
        // Same cooldown as the primary chat's Stop (#83855): the gateway may
        // still be winding down after this interrupt, so a quick edit/resend
        // on the tile must go interrupt-first even though busy already reads
        // false. Mark the runtime id (and any recovered id) before the RPC so
        // the window covers the whole wind-down.
        markSessionRecentlyInterrupted(runtimeId)
        await withSessionNotFoundResume(
          runtimeId,
          storedSessionIdForRuntime(runtimeId),
          liveId => requestGateway('session.interrupt', { session_id: liveId }),
          {
            requestGateway,
            onRecovered: recoveredId => {
              markSessionRecentlyInterrupted(recoveredId)
              rebindTileRuntime(runtimeId)(recoveredId)
            }
          }
        )
      },
      rehydrateTile: (storedSessionId, profile) => {
        const runtimeId = findSessionTile(storedSessionId, profile)?.runtimeId
        const mapped = runtimeIdByStoredSessionIdRef.current.get(storedSessionId)

        if (runtimeId && mapped === runtimeId) {
          runtimeIdByStoredSessionIdRef.current.delete(storedSessionId)
        }

        if (runtimeId) {
          sessionStateByRuntimeIdRef.current.delete(runtimeId)
          dropSessionState(runtimeId)
        }

        // Re-arm SessionTilePane's bounded resume effect without interrupting
        // the backend turn. Only this renderer's broken projection is discarded.
        patchSessionTile(storedSessionId, { error: undefined, runtimeId: undefined }, profile)
      },
      resumeTile: async (storedSessionId, profile) => {
        await ensureGatewayProfile(profile)
        const existing = findSessionTile(storedSessionId, profile)?.runtimeId
        const cached = existing ? sessionStateByRuntimeIdRef.current.get(existing) : undefined

        // A binding alone does not prove the tile owns a transcript. The
        // private cache can survive with an idle empty state; accepting it here
        // leaves an already-open tab permanently blank, while a new window works
        // because it takes the authoritative resume below. As in the primary
        // pane, no empty cache is valid transcript authority — `busy` only says
        // the backend turn is running, not that this renderer owns its history.
        if (
          existing &&
          cached?.storedSessionId === storedSessionId &&
          normalizeProfileKey(cached.profile) === normalizeProfileKey(profile) &&
          cached.messages.length > 0
        ) {
          publishSessionState(existing, cached)

          return existing
        }

        const [prefetch, resumed] = await Promise.all([
          getLatestSessionMessages(storedSessionId, profile).catch(() => null),
          requestGateway<SessionResumeResponse>('session.resume', {
            session_id: storedSessionId,
            cols: 96,
            omit_messages: true,
            ...(profile ? { profile } : {})
          })
        ])

        const runtimeId = resumed?.session_id

        if (!runtimeId) {
          throw new Error('resume returned no session id')
        }

        const persistedMessages = toChatMessages(prefetch?.messages ?? resumed?.messages ?? [])
        const hydratedMessages = appendLiveSessionProjection(persistedMessages, resumed)

        updateSessionState(
          runtimeId,
          state => ({
            ...state,
            busy: Boolean(resumed?.info?.running),
            messages: state.messages.length > 0 ? state.messages : hydratedMessages,
            profile: normalizeProfileKey(profile)
          }),
          storedSessionId
        )

        return runtimeId
      },
      submitToSession: async (runtimeId, text, profile) => {
        await ensureGatewayProfile(profile)
        await withSessionNotFoundResume(
          runtimeId,
          storedSessionIdForRuntime(runtimeId),
          liveId => requestGateway('prompt.submit', { session_id: liveId, text }, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS),
          { requestGateway, onRecovered: rebindTileRuntime(runtimeId) }
        )
      },
      updateSession: (runtimeId, updater) => updateSessionState(runtimeId, updater)
    })
  }, [
    archiveSession,
    branchStoredSession,
    executeSlashCommand,
    removeSession,
    requestGateway,
    runtimeIdByStoredSessionIdRef,
    sessionStateByRuntimeIdRef,
    updateSessionState
  ])
}
