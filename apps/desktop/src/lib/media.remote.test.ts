import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connection } from '@/store/session'

import {
  filePathFromMediaPath,
  gatewayFileUrl,
  gatewayMediaDataUrl,
  isGatewayLocalMediaPath,
  isRemoteGateway,
  mediaExternalUrl,
  openMediaExternal
} from './media'

describe('isRemoteGateway', () => {
  afterEach(() => {
    $connection.set(null)
  })

  it('is false with no connection', () => {
    $connection.set(null)
    expect(isRemoteGateway()).toBe(false)
  })

  it('is false in local mode', () => {
    $connection.set({ mode: 'local' } as never)
    expect(isRemoteGateway()).toBe(false)
  })

  it('is true in remote mode', () => {
    $connection.set({ mode: 'remote' } as never)
    expect(isRemoteGateway()).toBe(true)
  })
})

describe('filePathFromMediaPath', () => {
  it('passes through a plain path', () => {
    expect(filePathFromMediaPath('/home/u/.hermes/images/a.png')).toBe('/home/u/.hermes/images/a.png')
  })

  it('decodes a file:// URL with encoded characters', () => {
    expect(filePathFromMediaPath('file:///tmp/a%20b.png')).toBe('/tmp/a b.png')
  })
})

describe('isGatewayLocalMediaPath', () => {
  it('matches gateway-local file paths and excludes browser-native URLs', () => {
    expect(isGatewayLocalMediaPath('/home/u/out.pdf')).toBe(true)
    expect(isGatewayLocalMediaPath('file:///home/u/out.pdf')).toBe(true)
    expect(isGatewayLocalMediaPath('https://example.com/out.pdf')).toBe(false)
    expect(isGatewayLocalMediaPath('data:image/png;base64,abc')).toBe(false)
  })
})

describe('mediaExternalUrl', () => {
  afterEach(() => {
    $connection.set(null)
  })

  it('passes through http(s) URLs untouched', () => {
    $connection.set({ mode: 'remote', baseUrl: 'https://gw', token: 't' } as never)
    expect(mediaExternalUrl('https://example.com/a.png')).toBe('https://example.com/a.png')
  })

  it('keeps file:// form in local mode', () => {
    $connection.set({ mode: 'local' } as never)
    expect(mediaExternalUrl('/tmp/a.png')).toBe('file:///tmp/a.png')
    expect(mediaExternalUrl('file:///tmp/a.png')).toBe('file:///tmp/a.png')
  })

  it('rewrites gateway-local paths to an authenticated inline preview URL', () => {
    $connection.set({ mode: 'remote', baseUrl: 'https://gw', token: 's e/cret' } as never)
    expect(mediaExternalUrl('file:///tmp/a b.png')).toBe(
      'https://gw/api/fs/preview?path=%2Ftmp%2Fa%20b.png&token=s%20e%2Fcret'
    )
    expect(mediaExternalUrl('/tmp/a b.png')).toBe(
      'https://gw/api/fs/preview?path=%2Ftmp%2Fa%20b.png&token=s%20e%2Fcret'
    )
  })

  it('uses the remote preview endpoint instead of local file:// when remote connection lacks a token', () => {
    $connection.set({ mode: 'remote', baseUrl: 'https://gw', authMode: 'oauth' } as never)
    expect(mediaExternalUrl('/tmp/a.png')).toBe('https://gw/api/fs/preview?path=%2Ftmp%2Fa.png')
  })
})

describe('gatewayFileUrl', () => {
  afterEach(() => {
    $connection.set(null)
  })

  it('encodes the active desktop profile in the host so relative assets keep profile routing', () => {
    $connection.set({ mode: 'remote', profile: 'remote-docker' } as never)

    expect(gatewayFileUrl('/home/u/report dir/index.html')).toBe(
      'hermes-remote-file://remote-docker/home/u/report%20dir/index.html'
    )
  })

  it('uses a primary-profile sentinel when no desktop profile is selected', () => {
    $connection.set({ mode: 'remote' } as never)

    expect(gatewayFileUrl('file:///tmp/a%20b.html')).toBe('hermes-remote-file://_/tmp/a%20b.html')
  })
})

describe('openMediaExternal', () => {
  const openExternal = vi.fn(async () => undefined)
  const openPreviewInBrowser = vi.fn(async () => undefined)

  beforeEach(() => {
    openExternal.mockClear()
    openPreviewInBrowser.mockClear()
    vi.stubGlobal('window', { hermesDesktop: { openExternal, openPreviewInBrowser } })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    $connection.set(null)
  })

  it('opens remote gateway-local files through the remote preview bridge', async () => {
    $connection.set({ mode: 'remote', profile: 'remote-docker' } as never)

    await openMediaExternal('/home/u/report.html')

    expect(openPreviewInBrowser).toHaveBeenCalledWith('hermes-remote-file://remote-docker/home/u/report.html')
    expect(openExternal).not.toHaveBeenCalled()
  })

  it('keeps local files on the local openExternal path', async () => {
    $connection.set({ mode: 'local' } as never)

    await openMediaExternal('/tmp/local.txt')

    expect(openExternal).toHaveBeenCalledWith('file:///tmp/local.txt')
    expect(openPreviewInBrowser).not.toHaveBeenCalled()
  })
})

describe('gatewayMediaDataUrl', () => {
  const api = vi.fn(async () => ({ data_url: 'data:image/png;base64,ZHVtbXk=' }))

  beforeEach(() => {
    api.mockClear()
    vi.stubGlobal('window', { hermesDesktop: { api } })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('requests the encoded gateway path and returns the data URL', async () => {
    const url = await gatewayMediaDataUrl('/home/u/.hermes/images/a b.png')

    expect(url).toBe('data:image/png;base64,ZHVtbXk=')
    expect(api).toHaveBeenCalledWith({
      path: '/api/media?path=%2Fhome%2Fu%2F.hermes%2Fimages%2Fa%20b.png',
      profile: null
    })
  })
})
