import { afterEach, describe, expect, it, vi } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import { setActiveSessionId, setAwaitingResponse, setBusy } from '@/store/session'
import { clearAllSessionStates, publishSessionState } from '@/store/session-states'

const updateMocks = vi.hoisted(() => ({
  $backendUpdateApply: { get: vi.fn() },
  $backendUpdateStatus: { get: vi.fn() },
  $updateStatus: { get: vi.fn() },
  applyBackendUpdate: vi.fn(),
  applyUpdates: vi.fn(),
  cancelDesktopUpdatePreparation: vi.fn(),
  checkBackendUpdates: vi.fn(),
  checkUpdates: vi.fn(),
  discardDesktopUpdateStage: vi.fn(),
  getDesktopUpdateHistory: vi.fn(),
  getDesktopUpdateStage: vi.fn(),
  getDesktopUpstreamSyncStatus: vi.fn(),
  prepareDesktopUpdateStage: vi.fn(),
  restartAndApplyDesktopUpdateStage: vi.fn(),
  syncDesktopUpstream: vi.fn()
}))

vi.mock('@/store/updates', () => updateMocks)

const { host } = await import('./index')

afterEach(() => {
  vi.clearAllMocks()
})

describe('host.updates', () => {
  it('returns detached client and backend snapshots', () => {
    const clientStatus = {
      behind: 1,
      commits: [{ at: 1, author: 'Nous', sha: 'client-sha', summary: 'Client update' }],
      deployCommits: [{ at: 2, author: 'Axiom', sha: 'deploy-sha', summary: 'Axiom update' }],
      upstreamCommits: [{ at: 3, author: 'Nous', sha: 'upstream-sha', summary: 'Upstream update' }],
      upstreamAhead: 259,
      upstreamBehind: 18,
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
    expect(client?.deployCommits?.[0]).not.toBe(clientStatus.deployCommits[0])
    expect(client?.upstreamCommits?.[0]).not.toBe(clientStatus.upstreamCommits[0])
    expect(client?.upstreamAhead).toBe(259)
    expect(client?.upstreamBehind).toBe(18)
    expect(backend).not.toBe(backendStatus)
  })

  it('returns null when core has not published a snapshot yet', () => {
    updateMocks.$updateStatus.get.mockReturnValue(null)
    updateMocks.$backendUpdateStatus.get.mockReturnValue(null)

    expect(host.updates.getStatus('client')).toBeNull()
    expect(host.updates.getStatus('backend')).toBeNull()
  })

  it('exposes detached backend apply state and applies through the core backend updater', async () => {
    const applyState = {
      applying: true,
      stage: 'pull',
      message: 'Updating backend',
      percent: 42,
      error: null,
      command: null,
      log: [
        { at: 1, message: 'Fetching upstream…', stage: 'pull' },
        { at: 2, message: 'Installing dependencies…', stage: 'pull' }
      ]
    }

    updateMocks.$backendUpdateApply.get.mockReturnValue(applyState)
    updateMocks.applyBackendUpdate.mockResolvedValue({ ok: true })

    expect(host.updates.getBackendApply()).toEqual({
      applying: true,
      stage: 'pull',
      message: 'Updating backend',
      percent: 42,
      error: null,
      command: null,
      output: 'Fetching upstream…\nInstalling dependencies…'
    })
    expect(host.updates.getBackendApply()).not.toBe(applyState)
    await expect(host.updates.applyBackend()).resolves.toEqual({ ok: true })
    expect(updateMocks.applyBackendUpdate).toHaveBeenCalledOnce()
  })

  it('exposes the standard Desktop update through the existing guarded apply flow', async () => {
    updateMocks.applyUpdates.mockResolvedValue({ ok: true, handedOff: true })

    await expect(host.updates.standardUpdate()).resolves.toMatchObject({ ok: true })
    expect(updateMocks.applyUpdates).toHaveBeenCalledWith()

    updateMocks.applyUpdates.mockResolvedValueOnce({ ok: false, message: 'Updater did not launch.' })
    await expect(host.updates.standardUpdate()).rejects.toThrow('Updater did not launch.')

    updateMocks.applyUpdates.mockResolvedValueOnce({ ok: true, guiSkew: true, manualRestart: true })
    await expect(host.updates.standardUpdate()).resolves.toMatchObject({ ok: true, manualRestart: true })

    updateMocks.applyUpdates.mockResolvedValueOnce({ ok: true, manual: true, command: 'hermes update --branch axiom' })
    await expect(host.updates.standardUpdate()).resolves.toMatchObject({ ok: true, manual: true })
  })

  it('maps core stage/history records and rejects failed lifecycle results', async () => {
    updateMocks.getDesktopUpdateStage.mockResolvedValue({
      supported: true,
      phase: 'ready',
      output: 'Fetching Axiom…\nBuilding Desktop…',
      manifest: { baseSha: 'a'.repeat(40), targetSha: 'b'.repeat(40), branch: 'axiom', createdAt: 10 }
    })
    updateMocks.getDesktopUpdateHistory.mockResolvedValue([
      {
        id: 'one',
        at: 20,
        phase: 'apply',
        result: 'completed',
        baseSha: 'a'.repeat(40),
        targetSha: 'b'.repeat(40),
        commits: [{ sha: 'c'.repeat(40), subject: 'fix: update', author: 'Nous' }]
      }
    ])
    updateMocks.prepareDesktopUpdateStage.mockResolvedValue({
      ok: false,
      error: 'blocked',
      status: { message: 'Close the second Desktop window before preparing again.' }
    })

    await expect(host.updates.getStage()).resolves.toMatchObject({
      state: 'ready',
      branch: 'axiom',
      output: 'Fetching Axiom…\nBuilding Desktop…'
    })
    await expect(host.updates.getHistory()).resolves.toMatchObject([
      { id: 'one', result: 'completed', commits: [{ summary: 'fix: update' }] }
    ])
    await expect(host.updates.prepare()).rejects.toThrow('Close the second Desktop window before preparing again.')
  })

  it('preserves unsupported staging capability instead of erasing it as idle', async () => {
    updateMocks.getDesktopUpdateStage.mockResolvedValue({
      supported: false,
      phase: 'idle',
      message: 'Staged updates currently require Windows.'
    })

    await expect(host.updates.getStage()).resolves.toMatchObject({
      supported: false,
      state: 'available',
      message: 'Staged updates currently require Windows.'
    })
  })

  it('returns the initial preparing snapshot so Update Control can poll before the worker writes progress', async () => {
    updateMocks.prepareDesktopUpdateStage.mockResolvedValue({
      ok: true,
      status: {
        supported: true,
        phase: 'fetching',
        percent: 0,
        message: 'Preparing update while Desktop remains available.'
      }
    })

    await expect(host.updates.prepare()).resolves.toMatchObject({
      supported: true,
      state: 'preparing',
      phase: 'fetching',
      percent: 0,
      message: 'Preparing update while Desktop remains available.'
    })
  })

  it('publishes Hermes upstream through the named core operation and preserves stopped sync details', async () => {
    updateMocks.syncDesktopUpstream.mockResolvedValueOnce({
      ok: true,
      state: 'completed',
      message: 'Published 2 Hermes upstream commits to origin/axiom.'
    })
    await expect(host.updates.syncUpstream()).resolves.toMatchObject({ ok: true, state: 'completed' })

    updateMocks.getDesktopUpstreamSyncStatus.mockResolvedValue({
      running: true,
      startedAt: 10,
      output: '→ Fetching upstream…'
    })
    await expect(host.updates.getUpstreamSyncStatus()).resolves.toEqual({
      running: true,
      startedAt: 10,
      output: '→ Fetching upstream…'
    })

    updateMocks.syncDesktopUpstream.mockResolvedValueOnce({
      ok: false,
      state: 'handoff',
      error: 'reconciliation-stopped',
      message: 'Upstream reconciliation stopped safely.'
    })
    await expect(host.updates.syncUpstream()).resolves.toMatchObject({
      ok: false,
      state: 'handoff',
      message: 'Upstream reconciliation stopped safely.'
    })
  })

  it('exposes only named update operations, never branch, raw apply, progress, or bridge doors', () => {
    expect(Object.keys(host.updates).sort()).toEqual([
      'applyBackend',
      'cancelPreparation',
      'discardStage',
      'getBackendApply',
      'getHistory',
      'getStage',
      'getStatus',
      'getUpstreamSyncStatus',
      'prepare',
      'refresh',
      'restartAndApply',
      'standardUpdate',
      'syncUpstream'
    ])
    expect(host.updates).not.toHaveProperty('apply')
    expect(host.updates).not.toHaveProperty('setBranch')
    expect(host.updates).not.toHaveProperty('check')
    expect(host.updates).not.toHaveProperty('onProgress')
    expect(host.updates).not.toHaveProperty('bridge')
  })
})

describe('host.state turn flags', () => {
  afterEach(() => {
    setActiveSessionId(null)
    setBusy(false)
    setAwaitingResponse(false)
    clearAllSessionStates()
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
    $sessionTiles.set([{ profile: 'default', runtimeId: 'rt-tile-a', storedSessionId: 'tile-a' }])
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

describe('host.connections', () => {
  const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }
  const originalDesktop = desktopWindow.hermesDesktop

  const connection = (id: string, label: string) => ({
    id,
    kind: 'remote' as const,
    label,
    tokenPreview: null,
    tokenSet: true,
    url: `https://${id}.example`
  })

  const stubBridge = (list: () => Promise<unknown>) => {
    desktopWindow.hermesDesktop = {
      ...originalDesktop,
      connections: { list }
    } as unknown as Window['hermesDesktop']
  }

  afterEach(() => {
    desktopWindow.hermesDesktop = originalDesktop
  })

  it('returns the registry rows, not the envelope that carries them (#89823)', async () => {
    stubBridge(async () => ({
      connections: [connection('local', 'This Mac'), connection('homelab', 'Homelab')],
      primary: 'local',
      secureTokenStorage: true,
      version: 2
    }))

    const connections = await host.connections()

    expect(Array.isArray(connections)).toBe(true)
    expect(connections.map(entry => entry.id)).toEqual(['local', 'homelab'])
    expect(connections[1]).toMatchObject({ kind: 'remote', label: 'Homelab', url: 'https://homelab.example' })
  })

  it('folds the envelope-level primary id down onto the row that owns it', async () => {
    stubBridge(async () => ({
      connections: [connection('local', 'This Mac'), connection('homelab', 'Homelab')],
      primary: 'homelab',
      secureTokenStorage: true,
      version: 2
    }))

    expect((await host.connections()).map(entry => [entry.id, entry.primary])).toEqual([
      ['local', false],
      ['homelab', true]
    ])
  })

  it('reads as a single-source desktop when the payload carries no rows', async () => {
    stubBridge(async () => ({ primary: '', secureTokenStorage: true, version: 1 }))

    await expect(host.connections()).resolves.toEqual([])
  })

  it('still rejects on a Desktop build without the connection registry', async () => {
    desktopWindow.hermesDesktop = undefined

    await expect(host.connections()).rejects.toThrow('This Desktop build has no connection registry')
  })
})

describe('host workspace scope', () => {
  afterEach(async () => {
    host.setWorkspaceScope('sessions')
    const tree = await import('@/components/pane-shell/tree/store')
    tree.$newSessionTabAction.set(null)
    tree.removeTreePane('plugin-workspace:scope-test')
  })

  it('registers plugin workspace ownership and chrome options', async () => {
    const { registry } = await import('@/contrib/registry')

    const close = host.openWorkspace('scope-test', {
      dock: { pane: 'workspace', pos: 'right' },
      headerVeto: true,
      render: () => null,
      title: 'Scoped',
      uncloseable: true,
      workspaceMode: 'bots',
      workspaceOwnerKey: 'connection-a::default'
    })

    expect(registry.getArea('panes').find(pane => pane.id === 'plugin-workspace:scope-test')).toMatchObject({
      data: {
        dock: { pane: 'workspace', pos: 'right' },
        headerVeto: true,
        uncloseable: true
      },
      workspaceMode: 'bots',
      workspaceOwnerKey: 'connection-a::default'
    })

    close()
  })

  it('publishes the active workspace scope through one host seam', async () => {
    const { $workspaceMode, $workspaceOwnerKey } = await import('@/components/pane-shell/workspace-scope')

    expect(host.setWorkspaceScope('bots', 'connection-b::default')).toBe(true)
    expect($workspaceMode.get()).toBe('bots')
    expect($workspaceOwnerKey.get()).toBe('connection-b::default')
  })

  it('uses the shared tab action for an exact Bot owner without moving Sessions', async () => {
    const tree = await import('@/components/pane-shell/tree/store')
    const { $workspaceNewSessionTarget } = await import('@/components/pane-shell/workspace-scope')
    const opened: string[] = []

    const route = {
      connectionId: 'connection-b',
      mode: 'remote' as const,
      profile: 'writer',
      targetProfile: 'writer'
    }

    tree.$newSessionTabAction.set(() => opened.push('tab'))
    host.newChat(route, { workspaceMode: 'bots', workspaceOwnerKey: 'bot:connection-b::writer' })

    expect(opened).toEqual(['tab'])
    expect($workspaceNewSessionTarget.get()).toEqual({ kind: 'route', route })
  })
})
