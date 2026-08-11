import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import {
  type LiveSessionStatusResponse,
  rehydrateLiveSessionStatuses,
  resetLiveRuntimeTracking
} from '@/store/live-session-status'
import { $changeEventsAvailable, $cronChangeTick, $sessionsChangeTick } from '@/store/live-sync'
import { $onBattery, batteryPollInterval } from '@/store/power'
import { refreshActiveProfile } from '@/store/profile'
import { $activeSessionId, $currentCwd, setCurrentCwd } from '@/store/session'

import type { GatewayRequester } from '../types'

// Cron sessions are written by a background scheduler tick, messaging turns by
// the background gateway (Telegram, WeChat, Discord, …) — neither signals the
// desktop websocket directly. Backends with the change watcher broadcast
// `cron.changed` / `sessions.changed` when those on-disk writes land, so the
// timers below become slow safety-net backstops; against an older backend
// (no `change_events` on gateway.ready) they stay at the legacy cadence.
const CRON_POLL_INTERVAL_MS = 30_000
const CRON_BACKSTOP_INTERVAL_MS = 5 * 60_000
const MESSAGING_POLL_INTERVAL_MS = 10_000
const ACTIVE_MESSAGING_SESSION_POLL_INTERVAL_MS = 5_000
const ACTIVE_MESSAGING_SESSION_BACKSTOP_INTERVAL_MS = 30_000
// Match the TUI's live-session refresh cadence. Auto-compression can rotate a
// stored session id while its turn keeps running; until the next snapshot the
// sidebar row points at the new id while the renderer still knows the old one.
// A 15s cadence made that healthy transition look finished long enough to be
// alarming (and clicking the row appeared to "fix" it by touching the live
// session). This snapshot is small and already polled at 1.5s by the TUI.
const LIVE_SESSION_STATUS_POLL_INTERVAL_MS = 1_500
// With change events the snapshot re-pulls on every sessions.changed tick, so
// the interval only covers the degraded-socket edge the stream can't replay
// (see rehydrateLiveSessionStatuses) — 30s is plenty for that.
const LIVE_SESSION_STATUS_BACKSTOP_INTERVAL_MS = 30_000
// Coalesce tick-driven sidebar list refreshes: sessions.changed fires (floored
// to 2s server-side) on every state.db write during a streaming turn, and the
// full list refresh is heavier than the active_list snapshot. Trailing-edge
// scheduled, so the burst's last write always lands.
const SESSIONS_LIST_TICK_GAP_MS = 10_000

export { rehydrateLiveSessionStatuses, resetLiveRuntimeTracking }

interface BackgroundSyncParams {
  activeGatewayProfile: string
  activeIsMessaging: boolean
  activeSessionId: null | string
  freshDraftReady: boolean
  gatewayState: string
  refreshActiveMessagingTranscript: () => Promise<unknown> | unknown
  refreshCronJobs: () => Promise<unknown> | unknown
  refreshCurrentModel: (force?: boolean) => Promise<unknown> | unknown
  refreshHermesConfig: () => Promise<unknown> | unknown
  refreshMessagingSessions: () => Promise<unknown> | unknown
  refreshSessions: () => Promise<unknown> | unknown
  requestGateway: GatewayRequester
}

/** Poll a callback while the tab is visible, on `intervalMs`; re-checks on tab
 *  re-focus. On battery the cadence stretches (see store/power) — these are
 *  safety-net refreshes, not the live path, so they're the right thing to slow
 *  when the machine is spending its charge. Returns nothing — meant to live
 *  inside an effect. */
function visiblePoll(intervalMs: number, tick: () => void): () => void {
  const run = () => {
    if (document.visibilityState === 'visible') {
      tick()
    }
  }

  let intervalId = window.setInterval(run, batteryPollInterval(intervalMs, $onBattery.get()))

  const unsubscribeBattery = $onBattery.listen(onBattery => {
    window.clearInterval(intervalId)
    intervalId = window.setInterval(run, batteryPollInterval(intervalMs, onBattery))
  })

  document.addEventListener('visibilitychange', run)

  return () => {
    unsubscribeBattery()
    window.clearInterval(intervalId)
    document.removeEventListener('visibilitychange', run)
  }
}

/**
 * Keeps app data live while the gateway is open: an on-connect reseed (model /
 * profile / sessions + relative-cwd resolution), the cron / messaging /
 * open-transcript visibility polls, and the fresh-draft model/config reseed.
 * All the "the desktop websocket won't tell us, so poll" logic in one place.
 */
export function useBackgroundSync({
  activeGatewayProfile,
  activeIsMessaging,
  activeSessionId,
  freshDraftReady,
  gatewayState,
  refreshActiveMessagingTranscript,
  refreshCronJobs,
  refreshCurrentModel,
  refreshHermesConfig,
  refreshMessagingSessions,
  refreshSessions,
  requestGateway
}: BackgroundSyncParams): void {
  const changeEventsAvailable = useStore($changeEventsAvailable)
  const cronChangeTick = useStore($cronChangeTick)
  const sessionsChangeTick = useStore($sessionsChangeTick)

  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    void refreshCurrentModel()
    void refreshActiveProfile()
    void refreshSessions()

    // A RELATIVE workspace cwd (config `terminal.cwd: .`) renders as "." in the
    // file tree header — resolve it to the backend's absolute path once.
    // Session runtime info still overrides later, and never while a session is
    // active.
    const cwd = $currentCwd.get().trim()

    if (!$activeSessionId.get() && cwd && !/^(\/|[A-Za-z]:[\\/])/.test(cwd)) {
      void requestGateway<{ cwd?: string }>('config.get', { key: 'project', cwd })
        .then(info => {
          if (info.cwd && !$activeSessionId.get()) {
            setCurrentCwd(info.cwd)
          }
        })
        .catch(() => undefined)
    }
  }, [gatewayState, refreshCurrentModel, refreshSessions, requestGateway])

  // A reconnect loses renderer-only working/attention atoms while the backend
  // keeps the actual turns alive. Re-seed from the gateway's in-memory session
  // registry immediately, then re-pull on every sessions.changed broadcast; a
  // slow visible poll remains as the backstop for the degraded-socket edge the
  // stream cannot replay (legacy cadence against older backends).
  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    let cancelled = false
    let inFlight = false

    const refreshLiveStatuses = async () => {
      if (inFlight) {
        return
      }

      inFlight = true

      try {
        const response = await requestGateway<LiveSessionStatusResponse>('session.active_list', {})

        if (!cancelled) {
          rehydrateLiveSessionStatuses(response, Date.now(), activeGatewayProfile)
        }
      } catch {
        // Older gateways may not expose session.active_list. Live stream events
        // still work as before; leave the current sidebar state untouched.
      } finally {
        inFlight = false
      }
    }

    const dispose = visiblePoll(
      changeEventsAvailable ? LIVE_SESSION_STATUS_BACKSTOP_INTERVAL_MS : LIVE_SESSION_STATUS_POLL_INTERVAL_MS,
      () => void refreshLiveStatuses()
    )

    void refreshLiveStatuses()

    return () => {
      cancelled = true
      dispose()
    }
    // sessionsChangeTick: each sessions.changed broadcast re-seeds immediately
    // via the effect re-run (already coalesced to 2s server-side).
  }, [activeGatewayProfile, changeEventsAvailable, gatewayState, requestGateway, sessionsChangeTick])

  // sessions.changed also means the *stored* list may have new rows (a cron
  // run's session, an inbound messaging turn creating a thread). The full list
  // refresh is heavier than the active_list snapshot, so trail it on a gap
  // instead of firing per tick. Direct atom subscription: the throttle state
  // lives in the effect closure, not in refs synced from renders.
  useEffect(() => {
    if (gatewayState !== 'open' || !changeEventsAvailable) {
      return
    }

    let lastRunAt = 0
    let timer: null | number = null

    const run = () => {
      lastRunAt = Date.now()
      void refreshSessions()
      void refreshMessagingSessions()
    }

    const unsubscribe = $sessionsChangeTick.listen(() => {
      const since = Date.now() - lastRunAt

      if (since >= SESSIONS_LIST_TICK_GAP_MS) {
        run()
      } else if (timer === null) {
        timer = window.setTimeout(() => {
          timer = null
          run()
        }, SESSIONS_LIST_TICK_GAP_MS - since)
      }
    })

    return () => {
      unsubscribe()

      if (timer !== null) {
        window.clearTimeout(timer)
      }
    }
  }, [changeEventsAvailable, gatewayState, refreshMessagingSessions, refreshSessions])

  // Keep the cron-jobs section live without a user action (scheduler ticks in
  // the background). cron.changed (jobs.json moved: CRUD or a scheduler tick's
  // bookkeeping) drives the refresh; the visible poll is the backstop.
  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    if (cronChangeTick > 0) {
      void refreshCronJobs()
    }

    return visiblePoll(
      changeEventsAvailable ? CRON_BACKSTOP_INTERVAL_MS : CRON_POLL_INTERVAL_MS,
      () => void refreshCronJobs()
    )
  }, [changeEventsAvailable, cronChangeTick, gatewayState, refreshCronJobs])

  // Only the open messaging transcript needs its own cadence — local chats are
  // live over the websocket already. sessions.changed re-pulls it via the tick
  // dep; the visible poll is the backstop.
  useEffect(() => {
    if (gatewayState !== 'open' || !activeIsMessaging) {
      return
    }

    const dispose = visiblePoll(
      changeEventsAvailable ? ACTIVE_MESSAGING_SESSION_BACKSTOP_INTERVAL_MS : ACTIVE_MESSAGING_SESSION_POLL_INTERVAL_MS,
      () => void refreshActiveMessagingTranscript()
    )

    void refreshActiveMessagingTranscript()

    return dispose
    // sessionsChangeTick: an inbound turn re-pulls the open transcript.
  }, [activeIsMessaging, changeEventsAvailable, gatewayState, refreshActiveMessagingTranscript, sessionsChangeTick])

  // Messaging session lists against an older backend: no sessions.changed, so
  // keep the legacy visible poll. (Event-capable backends fold this into the
  // trailing sessions.changed refresh above.)
  useEffect(() => {
    if (gatewayState !== 'open' || changeEventsAvailable) {
      return
    }

    return visiblePoll(MESSAGING_POLL_INTERVAL_MS, () => void refreshMessagingSessions())
  }, [changeEventsAvailable, gatewayState, refreshMessagingSessions])

  // A fresh new-session draft (gateway open, no active session) re-pulls the
  // model + config so the composer pill reflects the profile default.
  useEffect(() => {
    if (gatewayState === 'open' && !activeSessionId && freshDraftReady) {
      void refreshCurrentModel()
      void refreshHermesConfig()
    }
  }, [activeSessionId, freshDraftReady, gatewayState, refreshCurrentModel, refreshHermesConfig])
}
