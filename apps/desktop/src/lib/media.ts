import { readDesktopFileDataUrl } from '@/lib/desktop-fs'
import { capitalize } from '@/lib/text'
import { $connection } from '@/store/session'

export type MediaKind = 'audio' | 'image' | 'video' | 'file'

interface MediaInfo {
  kind: MediaKind
  mime: string
}

const MEDIA_BY_EXT: Record<string, MediaInfo> = {
  avi: { kind: 'video', mime: 'video/x-msvideo' },
  bmp: { kind: 'image', mime: 'image/bmp' },
  flac: { kind: 'audio', mime: 'audio/flac' },
  gif: { kind: 'image', mime: 'image/gif' },
  jpeg: { kind: 'image', mime: 'image/jpeg' },
  jpg: { kind: 'image', mime: 'image/jpeg' },
  m4a: { kind: 'audio', mime: 'audio/mp4' },
  mkv: { kind: 'video', mime: 'video/x-matroska' },
  mov: { kind: 'video', mime: 'video/quicktime' },
  mp3: { kind: 'audio', mime: 'audio/mpeg' },
  mp4: { kind: 'video', mime: 'video/mp4' },
  ogg: { kind: 'audio', mime: 'audio/ogg' },
  opus: { kind: 'audio', mime: 'audio/ogg; codecs=opus' },
  png: { kind: 'image', mime: 'image/png' },
  svg: { kind: 'image', mime: 'image/svg+xml' },
  wav: { kind: 'audio', mime: 'audio/wav' },
  webm: { kind: 'video', mime: 'video/webm' },
  webp: { kind: 'image', mime: 'image/webp' }
}

function mediaInfo(path: string): MediaInfo | undefined {
  const ext = path.split(/[?#]/, 1)[0]?.split('.').pop()?.toLowerCase()

  return ext ? MEDIA_BY_EXT[ext] : undefined
}

export function mediaKind(path: string): MediaKind {
  return mediaInfo(path)?.kind ?? 'file'
}

export function mediaMime(path: string): string {
  return mediaInfo(path)?.mime ?? 'application/octet-stream'
}

export function mediaName(path: string): string {
  try {
    const url = new URL(path)

    return url.pathname.split('/').filter(Boolean).pop() || path
  } catch {
    return path.split(/[\\/]/).filter(Boolean).pop() || path
  }
}

export function mediaMarkdownHref(path: string): string {
  return `#media:${encodeURIComponent(path)}`
}

const REMOTE_FILE_PROTOCOL = 'hermes-remote-file'

function encodePathname(path: string): string {
  const normalized = filePathFromMediaPath(path)

  return normalized
    .split('/')
    .map((part, index) => (index === 0 && part === '' ? '' : encodeURIComponent(part)))
    .join('/')
}

// URL for gateway-local files rendered inside Desktop. The Electron main
// process handles this protocol by fetching bytes from the selected remote
// gateway with the active auth session, so Chromium never tries to read the
// backend path from the user's local disk. The profile lives in the hostname so
// relative HTML assets keep routing to the same backend profile.
export function gatewayFileUrl(path: string, profile?: null | string): string {
  const owner = encodeURIComponent(profile || $connection.get()?.profile || '_')
  const pathname = encodePathname(path)

  return `${REMOTE_FILE_PROTOCOL}://${owner}${pathname.startsWith('/') ? pathname : `/${pathname}`}`
}

export function isGatewayLocalMediaPath(path: string): boolean {
  return !/^(?:https?:|data:)/i.test(path) && (path.startsWith('file:') || path.startsWith('/'))
}

export function isInlineMediaSrc(path: string): boolean {
  return /^(?:https?|data):/i.test(path)
}

function isFileMediaPath(path: string): boolean {
  return /^(?:file:|\/|~\/|[a-z]:[\\/]|\\\\)/i.test(path)
}

export async function resolveMediaDisplaySrc(path: string): Promise<string> {
  if (isInlineMediaSrc(path) || !isFileMediaPath(path)) {
    return path
  }

  if (window.hermesDesktop && isRemoteGateway()) {
    return gatewayMediaDataUrl(path)
  }

  if (!window.hermesDesktop?.readFileDataUrl) {
    return mediaExternalUrl(path)
  }

  return window.hermesDesktop.readFileDataUrl(filePathFromMediaPath(path))
}

// Resolve a media path to a URL the shell can open. Remote mode rewrites
// gateway-local paths to an authenticated preview URL (the file lives on the
// gateway, not this disk); local mode keeps the file:// form.
export function mediaExternalUrl(path: string): string {
  if (/^https?:/i.test(path)) {
    return path
  }

  if (isRemoteGateway()) {
    const conn = $connection.get()

    if (conn?.baseUrl && isGatewayLocalMediaPath(path)) {
      const file = encodeURIComponent(filePathFromMediaPath(path))
      const token = conn.token ? `&token=${encodeURIComponent(conn.token)}` : ''

      return `${conn.baseUrl}/api/fs/preview?path=${file}${token}`
    }
  }

  return /^file:/i.test(path) ? path : `file://${path}`
}

export async function openMediaExternal(path: string, profile?: null | string): Promise<void> {
  if (isRemoteGateway() && isGatewayLocalMediaPath(path)) {
    if (window.hermesDesktop?.openPreviewInBrowser) {
      await window.hermesDesktop.openPreviewInBrowser(gatewayFileUrl(path, profile))

      return
    }

    await window.hermesDesktop?.openExternal(mediaExternalUrl(path))

    return
  }

  await window.hermesDesktop?.openExternal(mediaExternalUrl(path))
}

// Custom Electron scheme (registered in electron/main.ts) that streams a local
// file with Range support. Used for audio/video so playback bypasses the data
// URL size cap and supports seeking. `path` may be a plain path or `file://…`.
export function mediaStreamUrl(path: string): string {
  return `hermes-media://stream/${encodeURIComponent(filePathFromMediaPath(path))}`
}

export function mediaPathFromMarkdownHref(href?: string): string | null {
  if (!href?.startsWith('#media:')) {
    return null
  }

  try {
    return decodeURIComponent(href.slice('#media:'.length))
  } catch {
    return null
  }
}

export function filePathFromMediaPath(path: string): string {
  if (!path.startsWith('file:')) {
    return path
  }

  try {
    return decodeURIComponent(new URL(path).pathname)
  } catch {
    return path.replace(/^file:\/\//, '')
  }
}

// True when this desktop shell is wired to a remote gateway. Local media paths
// then live on the gateway machine, not this disk, so we fetch them over the API.
export function isRemoteGateway(): boolean {
  return $connection.get()?.mode === 'remote'
}

async function readGatewayFileDataUrl(path: string, profile?: null | string): Promise<string> {
  const activeProfile = profile ?? $connection.get()?.profile ?? undefined
  const request: { path: string; profile?: string } = {
    path: `/api/fs/read-data-url?path=${encodeURIComponent(path)}`
  }

  if (activeProfile) {
    request.profile = activeProfile
  }

  const result = await window.hermesDesktop!.api<string | { dataUrl?: string }>(request)

  return typeof result === 'string' ? result : result.dataUrl || ''
}

// Fetch gateway-local media as a data URL via the authenticated desktop FS
// bridge. Remote Desktop artifacts can live anywhere the gateway can read
// (workspace, skills, ~/.hermes/cache, etc.); /api/media is intentionally
// narrower and rejects non-images plus images outside its media roots.
export async function gatewayMediaDataUrl(path: string, profile?: null | string): Promise<string> {
  const file = filePathFromMediaPath(path)

  if (isRemoteGateway()) {
    return readGatewayFileDataUrl(file, profile)
  }

  return readDesktopFileDataUrl(file)
}

// Remote-mode replacement for opening gateway-local file paths with file://.
// The file lives on the gateway, so fetch it over the authenticated fs bridge
// and hand the bytes to the local browser shell as a download.
export async function downloadGatewayMediaFile(path: string, profile?: null | string): Promise<void> {
  const dataUrl = await gatewayMediaDataUrl(path, profile)

  if (!dataUrl) {
    throw new Error('Gateway returned no file data')
  }

  const response = await fetch(dataUrl)
  const blobUrl = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = blobUrl
  anchor.download = mediaName(path)
  anchor.rel = 'noopener noreferrer'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000)
}

export function mediaDisplayLabel(path: string): string {
  const escaped = mediaName(path).replace(/[[\]\\]/g, '\\$&')
  const kind = mediaKind(path)

  return `${capitalize(kind)}: ${escaped}`
}
