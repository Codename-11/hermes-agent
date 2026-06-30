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

// Resolve a media path to a URL the shell can open. Remote mode rewrites
// gateway-local paths to an authenticated /api/files/download URL (the file
// lives on the gateway, not this disk); local mode keeps the file:// form.
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

export async function openMediaExternal(path: string): Promise<void> {
  if (isRemoteGateway() && isGatewayLocalMediaPath(path)) {
    await window.hermesDesktop?.openPreviewInBrowser?.(gatewayFileUrl(path))

    return
  }

  await window.hermesDesktop?.openExternal(mediaExternalUrl(path))
}

// Custom Electron scheme (registered in electron/main.cjs) that streams a local
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

// Fetch a gateway-local image as a data URL via the authenticated REST bridge.
// Used in remote mode where readFileDataUrl (which reads THIS machine's disk)
// can't see files the agent wrote on the gateway. Requires the gateway to
// expose GET /api/media (hermes_cli/web_server.py).
export async function gatewayMediaDataUrl(path: string, profile?: null | string): Promise<string> {
  const file = filePathFromMediaPath(path)

  const result = await window.hermesDesktop!.api<{ data_url: string }>({
    path: `/api/media?path=${encodeURIComponent(file)}`,
    profile: profile ?? null
  })

  return result.data_url
}

export function mediaDisplayLabel(path: string): string {
  const escaped = mediaName(path).replace(/[[\]\\]/g, '\\$&')
  const kind = mediaKind(path)

  return `${kind[0].toUpperCase()}${kind.slice(1)}: ${escaped}`
}
