import { isGatewayReauthRequired, resolveGatewayWsUrl } from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import type { HermesGateway } from '@/hermes'
import { $gateway, ensureActiveGatewayOpen, isActivePrimary } from '@/store/gateway'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection, $gatewayState, setConnection } from '@/store/session'

function routeGatewayProfileParams(
  params: Record<string, unknown>,
  connection: { remoteProfile?: string } | null,
  activeProfile: string
): Record<string, unknown> {
  const requestedProfile = typeof params.profile === 'string' ? params.profile.trim() : ''
  const remoteProfile = connection?.remoteProfile?.trim()

  if (!requestedProfile || !remoteProfile || requestedProfile !== activeProfile.trim()) {
    return params
  }

  return { ...params, profile: remoteProfile }
}

export function useGatewayRequest() {
  const gatewayState = useStore($gatewayState)
  // Reactive companion to `gatewayRef`. The ref exists so `requestGateway`
  // keeps a stable identity and always reaches the live socket, but it is only
  // populated by the subscription effect below — i.e. AFTER the first render.
  // A component that reads `gatewayRef.current` while rendering therefore sees
  // null on mount, and if the connection state doesn't happen to flip
  // afterwards it never re-renders to pick the instance up. Anything that needs
  // the gateway as a render-time VALUE (props, memo deps) must use this.
  const gateway = useStore($gateway) as HermesGateway | null
  const gatewayRef = useRef<HermesGateway | null>(null)

  const connectionRef = useRef<Awaited<ReturnType<NonNullable<typeof window.hermesDesktop>['getConnection']>> | null>(
    null
  )

  const gatewayStateRef = useRef(gatewayState)
  const reconnectingRef = useRef<Promise<HermesGateway | null> | null>(null)
  // Holds the reauth error from the most recent failed reconnect so
  // requestGateway can surface the gateway's "session expired, sign in again"
  // message instead of the opaque "connection closed" that triggered the retry.
  const reauthErrorRef = useRef<unknown>(null)

  // eslint-disable-next-line no-restricted-syntax -- legitimate non-atom ref write (see eslint rule comment)
  useEffect(() => {
    gatewayStateRef.current = gatewayState
  }, [gatewayState])

  // Track the active gateway (primary or a background profile's socket) so
  // outbound requests and overlay props always target the focused profile.
  useEffect(
    () =>
      $gateway.subscribe(gateway => {
        gatewayRef.current = gateway as HermesGateway | null
      }),
    []
  )

  const ensureGatewayOpen = useCallback(async () => {
    const existing = gatewayRef.current

    if (!existing) {
      return null
    }

    if (gatewayStateRef.current === 'open') {
      return existing
    }

    if (reconnectingRef.current) {
      return reconnectingRef.current
    }

    reconnectingRef.current = (async () => {
      const desktop = window.hermesDesktop

      if (!desktop) {
        return null
      }

      reauthErrorRef.current = null

      try {
        // Reconnect to whichever profile the gateway is currently routed to (not
        // always the primary), so a sleep/wake reconnect keeps the user on the
        // profile they were chatting in.
        const conn = await desktop.getConnection($activeGatewayProfile.get())
        connectionRef.current = conn
        setConnection(conn)
        // Re-mint the WS URL before reconnecting. OAuth tickets are single-use
        // and short-lived, so the cached conn.wsUrl ticket is dead here;
        // resolveGatewayWsUrl() never connects with a stale ticket. An explicit
        // auth rejection becomes a reauth error; transport failures remain
        // retryable. Stash only the former so requestGateway can show the
        // actionable "sign in again" message.
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await existing.connect(wsUrl)

        return existing
      } catch (error) {
        if (isGatewayReauthRequired(error)) {
          reauthErrorRef.current = error
        }

        connectionRef.current = null
        setConnection(null)

        return null
      } finally {
        reconnectingRef.current = null
      }
    })()

    return reconnectingRef.current
  }, [])

  const requestGateway = useCallback(
    async <T>(method: string, params: Record<string, unknown> = {}, timeoutMs?: number, signal?: AbortSignal) => {
      const gateway = gatewayRef.current
      const routedParams = routeGatewayProfileParams(params, $connection.get(), $activeGatewayProfile.get())

      if (!gateway) {
        throw new Error('Hermes gateway unavailable')
      }

      try {
        return await gateway.request<T>(method, routedParams, timeoutMs, signal)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)

        if (!/not connected|connection closed/i.test(message)) {
          throw error
        }

        // The request belongs to the gateway captured above. A profile switch
        // can replace gatewayRef while this await is pending; recovering via the
        // newly active gateway would retry A's RPC against B's backend. Let the
        // newer navigation own recovery instead of cross-wiring profiles.
        if (gatewayRef.current !== gateway) {
          throw error
        }

        // Primary keeps the OAuth-aware reconnect (remote gateways re-mint a
        // single-use ticket); background profiles are always local pool
        // backends, so the registry handles their reconnect with no reauth.
        const recovered = isActivePrimary() ? await ensureGatewayOpen() : await ensureActiveGatewayOpen()

        if (!recovered) {
          // Prefer the reauth error from the failed reconnect (OAuth session
          // expired) over the generic transport error that triggered the retry.
          const reauthError = reauthErrorRef.current
          reauthErrorRef.current = null

          if (reauthError) {
            throw reauthError
          }

          throw error
        }

        return recovered.request<T>(method, routedParams, timeoutMs, signal)
      }
    },
    [ensureGatewayOpen]
  )

  return { connectionRef, gateway, gatewayRef, requestGateway }
}
