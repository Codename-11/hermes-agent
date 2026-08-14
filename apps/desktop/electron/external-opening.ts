import { type ChildProcess, spawn } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

interface ExternalShell {
  openExternal: (url: string) => Promise<unknown>
  openPath: (filePath: string) => Promise<string>
  showItemInFolder: (filePath: string) => void
}

interface RemoteConnection {
  mode?: string
  baseUrl?: string
  authMode?: string
  token?: string | null
  remoteProfile?: string
}

interface BinaryDownload {
  buffer: Buffer
  headers?: Record<string, string | string[] | undefined>
}

export interface ExternalOpeningDeps {
  isWsl: boolean
  tempDir: string
  shell: ExternalShell
  rememberLog: (message: string) => void
  resolveRequestedPath: (value: string, options: { purpose: string }) => string
  ensureBackend: (profile?: string | null) => Promise<RemoteConnection>
  routeRemotePath: (value: string, profile?: string | null, remoteProfile?: string) => string
  fetchBinary: (url: string, token: string | null | undefined, options: { timeoutMs: number }) => Promise<BinaryDownload>
  fetchBinaryViaOauthSession: (url: string, options: { timeoutMs: number }) => Promise<BinaryDownload>
  timeoutMs: number
  spawnProcess?: (command: string, args: string[], options: Record<string, unknown>) => Pick<ChildProcess, 'on' | 'unref'>
  now?: () => number
  randomSuffix?: () => string
}

export interface ExternalOpening {
  openExternalUrl: (rawUrl: unknown) => boolean
  openPreviewInBrowser: (rawUrl: unknown) => Promise<boolean>
  openRemoteFile: (payload?: { path?: string; profile?: string | null }) => Promise<{ ok: true; path: string }>
}

/** Native URL and remote-artifact opening policy, isolated from Electron registration. */
export function createExternalOpening(deps: ExternalOpeningDeps): ExternalOpening {
  const spawnProcess = deps.spawnProcess ?? ((command, args, options) => spawn(command, args, options))
  const now = deps.now ?? Date.now
  const randomSuffix = deps.randomSuffix ?? (() => crypto.randomBytes(4).toString('hex'))

  function openExternalUrl(rawUrl: unknown): boolean {
    const raw = String(rawUrl || '').trim()
    if (!raw) {
      return false
    }

    let parsed: URL
    try {
      parsed = new URL(raw)
    } catch {
      return false
    }

    if (parsed.protocol === 'file:') {
      let localPath: string
      try {
        localPath = deps.resolveRequestedPath(parsed.toString(), { purpose: 'Open external file' })
      } catch {
        return false
      }

      void deps.shell
        .openPath(localPath)
        .then(error => {
          if (!error) {
            return
          }

          deps.rememberLog(`[file] openPath failed: ${error}; revealing in folder instead`)
          try {
            deps.shell.showItemInFolder(localPath)
          } catch (error: any) {
            deps.rememberLog(`[file] showItemInFolder failed: ${error.message}`)
          }
        })
        .catch((error: any) => deps.rememberLog(`[file] openPath rejected: ${error.message}`))

      return true
    }

    if (!['http:', 'https:', 'mailto:'].includes(parsed.protocol)) {
      return false
    }

    const url = parsed.toString()
    if (deps.isWsl) {
      deps.rememberLog(`[link] opening via WSL→Windows: ${url}`)
      const child = spawnProcess('cmd.exe', ['/c', 'start', '""', url], {
        detached: true,
        stdio: 'ignore',
        windowsHide: true
      })
      child.on('error', (error: Error) => {
        deps.rememberLog(`[link] cmd.exe start failed: ${error.message}; falling back to xdg-open`)
        deps.shell
          .openExternal(url)
          .catch((fallback: any) => deps.rememberLog(`[link] xdg-open failed: ${fallback.message}`))
      })
      child.unref()
      return true
    }

    deps.shell
      .openExternal(url)
      .catch((error: any) => deps.rememberLog(`[link] openExternal failed: ${error.message}`))
    return true
  }

  function remoteFilePath(rawPath: unknown): string {
    const value = String(rawPath || '').trim()
    if (!value) {
      return ''
    }
    if (!value.startsWith('file:')) {
      return value
    }

    try {
      return decodeURIComponent(new URL(value).pathname)
    } catch {
      return value.replace(/^file:\/\//, '')
    }
  }

  function safeDownloadFilename(rawName: unknown): string {
    const base = String(rawName || '')
      .split(/[\\/]/)
      .filter(Boolean)
      .pop()
    const cleaned = String(base || 'artifact')
      // Match Windows-invalid filename characters plus ASCII controls.
      // eslint-disable-next-line no-control-regex
      .replace(/[<>:"/\\|?*\x00-\x1F]/g, '_')
      .trim()
      .slice(0, 160)
    return cleaned || 'artifact'
  }

  function headerValue(headers: BinaryDownload['headers'], name: string): string {
    const wanted = name.toLowerCase()
    for (const [key, value] of Object.entries(headers || {})) {
      if (key.toLowerCase() !== wanted) {
        continue
      }
      return Array.isArray(value) ? String(value[0] || '') : String(value || '')
    }
    return ''
  }

  function filenameFromContentDisposition(value: unknown): string {
    const header = String(value || '')
    const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
    if (encoded) {
      try {
        return decodeURIComponent(encoded.replace(/^"|"$/g, ''))
      } catch {
        return encoded.replace(/^"|"$/g, '')
      }
    }
    return header.match(/filename="?([^";]+)"?/i)?.[1] || ''
  }

  async function openDownloadedFile(
    buffer: Buffer,
    remotePath: string,
    headers: BinaryDownload['headers'] = {}
  ): Promise<{ ok: true; path: string }> {
    const dir = path.join(deps.tempDir, 'hermes-remote-files')
    fs.mkdirSync(dir, { recursive: true })
    const filename = safeDownloadFilename(
      filenameFromContentDisposition(headerValue(headers, 'content-disposition')) || remotePath
    )
    const target = path.join(dir, `${now()}-${randomSuffix()}-${filename}`)
    fs.writeFileSync(target, buffer)

    const error = await deps.shell.openPath(target)
    if (error) {
      deps.rememberLog(`[file] openPath failed for downloaded remote file: ${error}; revealing in folder instead`)
      deps.shell.showItemInFolder(target)
    }

    return { ok: true, path: target }
  }

  async function openRemoteFile(
    payload: { path?: string; profile?: string | null } = {}
  ): Promise<{ ok: true; path: string }> {
    const remotePath = remoteFilePath(payload.path)
    if (!remotePath) {
      throw new Error('Remote file path is required')
    }

    const connection = await deps.ensureBackend(payload.profile)
    if (connection.mode !== 'remote') {
      throw new Error('Remote file opening requires a remote gateway connection')
    }

    const base = String(connection.baseUrl || '').replace(/\/+$/, '')
    const downloadPath = deps.routeRemotePath(
      `/api/files/download?path=${encodeURIComponent(remotePath)}`,
      payload.profile,
      connection.remoteProfile
    )
    const url = `${base}${downloadPath}`
    const download =
      connection.authMode === 'oauth'
        ? await deps.fetchBinaryViaOauthSession(url, { timeoutMs: deps.timeoutMs })
        : await deps.fetchBinary(url, connection.token, { timeoutMs: deps.timeoutMs })

    return openDownloadedFile(download.buffer, remotePath, download.headers)
  }

  async function openPreviewInBrowser(rawUrl: unknown): Promise<boolean> {
    const raw = String(rawUrl || '').trim()
    if (!raw) {
      return false
    }

    let parsed: URL
    try {
      parsed = new URL(raw)
    } catch {
      return false
    }

    if (parsed.protocol === 'file:') {
      let localPath: string
      try {
        localPath = deps.resolveRequestedPath(parsed.toString(), { purpose: 'Open preview in browser' })
      } catch {
        return false
      }

      await deps.shell.openExternal(pathToFileURL(localPath).toString())
      return true
    }

    return openExternalUrl(raw)
  }

  return { openExternalUrl, openPreviewInBrowser, openRemoteFile }
}
