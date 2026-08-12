import { afterEach, describe, expect, it, vi } from 'vitest'

const updateMocks = vi.hoisted(() => ({
  $backendUpdateApply: { get: vi.fn() },
  $backendUpdateStatus: { get: vi.fn() },
  $updateStatus: { get: vi.fn() },
  applyBackendUpdate: vi.fn(),
  checkBackendUpdates: vi.fn(),
  checkUpdates: vi.fn(),
  discardDesktopUpdateStage: vi.fn(),
  getDesktopUpdateHistory: vi.fn(),
  getDesktopUpdateStage: vi.fn(),
  prepareDesktopUpdateStage: vi.fn(),
  restartAndApplyDesktopUpdateStage: vi.fn()
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
    expect(backend).not.toBe(backendStatus)
  })

  it('returns null when core has not published a snapshot yet', () => {
    updateMocks.$updateStatus.get.mockReturnValue(null)
    updateMocks.$backendUpdateStatus.get.mockReturnValue(null)

    expect(host.updates.getStatus('client')).toBeNull()
    expect(host.updates.getStatus('backend')).toBeNull()
  })

  it('exposes detached backend apply state and applies through the core backend updater', async () => {
    const applyState = { applying: true, stage: 'pull', message: 'Updating backend', percent: 42, error: null, command: null }
    updateMocks.$backendUpdateApply.get.mockReturnValue(applyState)
    updateMocks.applyBackendUpdate.mockResolvedValue({ ok: true })

    expect(host.updates.getBackendApply()).toEqual(applyState)
    expect(host.updates.getBackendApply()).not.toBe(applyState)
    await expect(host.updates.applyBackend()).resolves.toEqual({ ok: true })
    expect(updateMocks.applyBackendUpdate).toHaveBeenCalledOnce()
  })

  it('maps core stage/history records and rejects failed lifecycle results', async () => {
    updateMocks.getDesktopUpdateStage.mockResolvedValue({
      supported: true,
      phase: 'ready',
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

    await expect(host.updates.getStage()).resolves.toMatchObject({ state: 'ready', branch: 'axiom' })
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

  it('exposes only the named staged lifecycle, never branch, raw apply, progress, or bridge doors', () => {
    expect(Object.keys(host.updates).sort()).toEqual([
      'applyBackend',
      'discardStage',
      'getBackendApply',
      'getHistory',
      'getStage',
      'getStatus',
      'prepare',
      'refresh',
      'restartAndApply'
    ])
    expect(host.updates).not.toHaveProperty('apply')
    expect(host.updates).not.toHaveProperty('setBranch')
    expect(host.updates).not.toHaveProperty('check')
    expect(host.updates).not.toHaveProperty('onProgress')
    expect(host.updates).not.toHaveProperty('bridge')
  })
})
