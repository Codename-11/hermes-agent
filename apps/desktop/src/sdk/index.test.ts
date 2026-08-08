import { afterEach, describe, expect, it, vi } from 'vitest'

const updateMocks = vi.hoisted(() => ({
  $backendUpdateStatus: { get: vi.fn() },
  $updateStatus: { get: vi.fn() },
  openUpdatesWindow: vi.fn()
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
