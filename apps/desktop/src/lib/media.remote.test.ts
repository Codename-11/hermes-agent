import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connection } from '@/store/session'

import { filePathFromMediaPath, gatewayMediaDataUrl, isGatewayLocalMediaPath, isRemoteGateway, mediaExternalUrl } from './media'

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

  it('rewrites gateway-local paths to an authenticated download URL', () => {
    $connection.set({ mode: 'remote', baseUrl: 'https://gw', token: 's e/cret' } as never)
    expect(mediaExternalUrl('file:///tmp/a b.png')).toBe(
      'https://gw/api/files/download?path=%2Ftmp%2Fa%20b.png&token=s%20e%2Fcret'
    )
    expect(mediaExternalUrl('/tmp/a b.png')).toBe(
      'https://gw/api/files/download?path=%2Ftmp%2Fa%20b.png&token=s%20e%2Fcret'
    )
  })

  it('falls back to file:// when remote connection lacks a token', () => {
    $connection.set({ mode: 'remote', baseUrl: 'https://gw' } as never)
    expect(mediaExternalUrl('/tmp/a.png')).toBe('file:///tmp/a.png')
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
    $connection.set(null)
  })

  it('requests the encoded gateway path against the active remote profile', async () => {
    $connection.set({ mode: 'remote', profile: 'coder' } as never)

    const url = await gatewayMediaDataUrl('/home/u/.hermes/images/a b.png')

    expect(url).toBe('data:image/png;base64,ZHVtbXk=')
    expect(api).toHaveBeenCalledWith({
      path: '/api/media?path=%2Fhome%2Fu%2F.hermes%2Fimages%2Fa%20b.png',
      profile: 'coder'
    })
  })

  it('lets artifact-owned profiles override the active gateway profile', async () => {
    $connection.set({ mode: 'remote', profile: 'default' } as never)

    await gatewayMediaDataUrl('/home/u/.hermes/images/a b.png', 'remote-art')

    expect(api).toHaveBeenCalledWith({
      path: '/api/media?path=%2Fhome%2Fu%2F.hermes%2Fimages%2Fa%20b.png',
      profile: 'remote-art'
    })
  })

  it('omits profile when no active gateway profile is known', async () => {
    await gatewayMediaDataUrl('/home/u/.hermes/images/a b.png')

    expect(api).toHaveBeenCalledWith({
      path: '/api/media?path=%2Fhome%2Fu%2F.hermes%2Fimages%2Fa%20b.png'
    })
  })
})
