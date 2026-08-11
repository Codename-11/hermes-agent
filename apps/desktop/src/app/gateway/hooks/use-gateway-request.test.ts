import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
import { $gateway } from '@/store/gateway'

import { useGatewayRequest } from './use-gateway-request'

const fakeGateway = { connectionState: 'open' } as unknown as HermesGateway

afterEach(() => {
  $gateway.set(null)
})

describe('useGatewayRequest', () => {
  // The composer's `/` completions only exist when ChatBar receives a non-null
  // gateway PROP. `gatewayRef` is populated by a subscription effect, so it is
  // still null on the first render — a surface that read the ref while
  // rendering (session tiles / ⌘T tabs) shipped `gateway={null}` and silently
  // lost slash completions. The returned `gateway` value must be live
  // immediately so that never happens again.
  it('exposes the live gateway on the first render, before effects run', () => {
    $gateway.set(fakeGateway)

    const { result } = renderHook(() => useGatewayRequest())

    expect(result.current.gateway).toBe(fakeGateway)
  })

  it('tracks the gateway when the active socket changes', () => {
    const { result } = renderHook(() => useGatewayRequest())

    expect(result.current.gateway).toBeNull()

    act(() => $gateway.set(fakeGateway))

    expect(result.current.gateway).toBe(fakeGateway)
  })

  it('does not retry an old request through a newly active profile gateway', async () => {
    let rejectOld!: (error: Error) => void
    const oldGateway = {
      connectionState: 'open',
      request: vi.fn(
        () =>
          new Promise((_resolve, reject) => {
            rejectOld = reject
          })
      )
    } as unknown as HermesGateway
    const newGateway = {
      connectionState: 'open',
      request: vi.fn()
    } as unknown as HermesGateway

    $gateway.set(oldGateway)
    const { result } = renderHook(() => useGatewayRequest())
    const pending = result.current.requestGateway('session.resume', { session_id: 'stored-a' })

    act(() => $gateway.set(newGateway))
    rejectOld(new Error('connection closed'))

    await expect(pending).rejects.toThrow('connection closed')
    expect(oldGateway.request).toHaveBeenCalledOnce()
    expect(newGateway.request).not.toHaveBeenCalled()
  })
})
