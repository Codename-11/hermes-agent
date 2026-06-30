import { afterEach, describe, expect, it, vi } from 'vitest'

import { $connection } from '@/store/session'

import { localPreviewTarget, normalizeOrLocalPreviewTarget } from './local-preview'

describe('localPreviewTarget remote gateway paths', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    $connection.set(null)
  })

  it('builds remote-file URLs instead of local file:// URLs in remote mode', () => {
    $connection.set({ mode: 'remote', profile: 'remote-docker' } as never)

    const target = localPreviewTarget('reports/a b.html', '/home/tgi/project')

    expect(target).toMatchObject({
      kind: 'file',
      path: '/home/tgi/project/reports/a b.html',
      previewKind: 'html',
      url: 'hermes-remote-file://remote-docker/home/tgi/project/reports/a%20b.html'
    })
  })

  it('does not ask Electron main to normalize backend-local paths in remote mode', async () => {
    const normalizePreviewTarget = vi.fn(async () => ({
      kind: 'file',
      label: 'wrong.html',
      path: '/local/wrong.html',
      previewKind: 'html',
      source: '/local/wrong.html',
      url: 'file:///local/wrong.html'
    }))
    $connection.set({ mode: 'remote' } as never)
    vi.stubGlobal('window', { hermesDesktop: { normalizePreviewTarget } })

    const target = await normalizeOrLocalPreviewTarget('/srv/backend/report.html')

    expect(normalizePreviewTarget).not.toHaveBeenCalled()
    expect(target?.path).toBe('/srv/backend/report.html')
    expect(target?.url).toBe('hermes-remote-file://_/srv/backend/report.html')
  })
})
