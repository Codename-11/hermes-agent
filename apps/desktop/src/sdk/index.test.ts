import { afterEach, describe, expect, it, vi } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import { host } from '@/sdk'
import { setActiveSessionId, setAwaitingResponse, setBusy } from '@/store/session'
import { clearAllSessionStates, publishSessionState } from '@/store/session-states'

const updateMocks = vi.hoisted(() => ({
  $backendUpdateStatus: { get: vi.fn() },
  $updateStatus: { get: vi.fn() },
  openUpdatesWindow: vi.fn()
}))

vi.mock('@/store/updates', () => updateMocks)

describe('host.state turn flags', () => {
  afterEach(() => {
    setActiveSessionId(null)
    setBusy(false)
    setAwaitingResponse(false)
    clearAllSessionStates()
    vi.clearAllMocks()
  })

  it('uses the draft atoms when there is no runtime session', () => {
    expect(host.state.busy.get()).toBe(false)
    expect(host.state.awaitingResponse.get()).toBe(false)

    setBusy(true)
    setAwaitingResponse(true)

    expect(host.state.busy.get()).toBe(true)
    expect(host.state.awaitingResponse.get()).toBe(true)
  })

  it('reads the focused session slice once a runtime exists', () => {
    setBusy(false)
    setAwaitingResponse(false)
    setActiveSessionId('rt-focus')
    publishSessionState('rt-focus', {
      ...createClientSessionState('stored-focus'),
      awaitingResponse: true,
      busy: true
    })

    expect(host.state.busy.get()).toBe(true)
    expect(host.state.awaitingResponse.get()).toBe(true)

    publishSessionState('rt-focus', {
      ...createClientSessionState('stored-focus'),
      awaitingResponse: false,
      busy: true
    })

    expect(host.state.busy.get()).toBe(true)
    expect(host.state.awaitingResponse.get()).toBe(false)
  })

  it('does not pick up a background session', () => {
    setActiveSessionId('rt-focus')
    publishSessionState('rt-focus', createClientSessionState('stored-focus'))
    publishSessionState('rt-bg', {
      ...createClientSessionState('stored-bg'),
      awaitingResponse: true,
      busy: true
    })

    expect(host.state.busy.get()).toBe(false)
    expect(host.state.awaitingResponse.get()).toBe(false)
  })

  it('follows a focused session tile, not the primary', async () => {
    const tree = await import('@/components/pane-shell/tree/store')
    const model = await import('@/components/pane-shell/tree/model')
    const { registry } = await import('@/contrib/registry')
    const { $sessionTiles } = await import('@/store/session-states')

    // A second chat zone holding a session tile, next to the main workspace.
    for (const id of ['workspace', 'session-tile:tile-a']) {
      registry.register({
        area: 'panes',
        data: id === 'workspace' ? { placement: 'main', uncloseable: true } : { placement: 'main' },
        id,
        render: () => null,
        title: id
      })
    }

    tree.declareDefaultTree(
      model.split('row', [
        model.group(['workspace'], { active: 'workspace', id: 'grp-main' }),
        model.group(['session-tile:tile-a'], { active: 'session-tile:tile-a', id: 'grp-side' })
      ])
    )

    // Primary chat is idle; the tile's session is mid-turn.
    setActiveSessionId('rt-primary')
    publishSessionState('rt-primary', createClientSessionState('stored-primary'))
    $sessionTiles.set([{ runtimeId: 'rt-tile-a', storedSessionId: 'tile-a' }])
    publishSessionState('rt-tile-a', {
      ...createClientSessionState('tile-a'),
      awaitingResponse: true,
      busy: true
    })

    // Focusing the tile zone moves the flags onto the tile's session…
    tree.noteActiveTreeGroup('grp-side')
    expect(host.state.busy.get()).toBe(true)
    expect(host.state.awaitingResponse.get()).toBe(true)

    // …and homing back to the workspace returns to the (idle) primary.
    tree.noteActiveTreeGroup('grp-main')
    expect(host.state.busy.get()).toBe(false)
    expect(host.state.awaitingResponse.get()).toBe(false)

    $sessionTiles.set([])
  })
})

describe('host.updates', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('returns detached client and backend snapshots', () => {
    const clientStatus = {
      behind: 1,
      commits: [{ at: 1, author: 'Nous', sha: 'client-sha', summary: 'Client update' }],
      fetchedAt: 1,
      supported: true
    }
    const backendStatus = { behind: 2, fetchedAt: 2, supported: true }
    updateMocks.$updateStatus.get.mockReturnValue(clientStatus)
    updateMocks.$backendUpdateStatus.get.mockReturnValue(backendStatus)

    const client = host.updates.getStatus('client')
    const backend = host.updates.getStatus('backend')

    expect(client).toEqual(clientStatus)
    expect(backend).toEqual(backendStatus)
    expect(client).not.toBe(clientStatus)
    expect(client?.commits).not.toBe(clientStatus.commits)
    expect(client?.commits?.[0]).not.toBe(clientStatus.commits[0])
    expect(backend).not.toBe(backendStatus)
  })

  it('returns null when core has not published a snapshot yet', () => {
    updateMocks.$updateStatus.get.mockReturnValue(null)
    updateMocks.$backendUpdateStatus.get.mockReturnValue(null)

    expect(host.updates.getStatus('client')).toBeNull()
    expect(host.updates.getStatus('backend')).toBeNull()
  })

  it('opens the core-owned updater for the active target', () => {
    host.updates.open()

    expect(updateMocks.openUpdatesWindow).toHaveBeenCalledOnce()
  })

  it('does not expose mutation, branch, check, progress, or raw bridge doors', () => {
    expect(Object.keys(host.updates).sort()).toEqual(['getStatus', 'open'])
    expect(host.updates).not.toHaveProperty('apply')
    expect(host.updates).not.toHaveProperty('setBranch')
    expect(host.updates).not.toHaveProperty('check')
    expect(host.updates).not.toHaveProperty('onProgress')
    expect(host.updates).not.toHaveProperty('bridge')
  })
})
