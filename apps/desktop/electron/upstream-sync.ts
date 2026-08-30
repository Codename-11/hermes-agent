import { type ChildProcess, execFileSync, spawn } from 'node:child_process'
import path from 'node:path'

export interface DesktopUpstreamSyncResult {
  ok: boolean
  state: 'completed' | 'failed' | 'handoff'
  branch?: string
  reconciled?: number
  targetSha?: string
  message: string
  error?: string
  worktree?: string
  reportPath?: string
  output?: string
}

export interface DesktopUpstreamSyncStatus {
  running: boolean
  startedAt?: number
  output?: string
  result?: DesktopUpstreamSyncResult
}

const RESULT_PREFIX = 'HERMES_UPSTREAM_SYNC_RESULT='
const MAX_OUTPUT = 24_000
const DEFAULT_TIMEOUT_MS = 30 * 60 * 1_000

let active: Promise<DesktopUpstreamSyncResult> | null = null
let status: DesktopUpstreamSyncStatus = { running: false }

function cleanOutput(output: string): string | undefined {
  const cleaned = output
    .split(/\r?\n/)
    .filter(line => !line.startsWith(RESULT_PREFIX))
    .join('\n')
    .trim()

  return cleaned || undefined
}

export function getUpstreamSyncStatus(): DesktopUpstreamSyncStatus {
  return {
    ...status,
    result: status.result ? { ...status.result } : undefined
  }
}

export function appendUpstreamSyncOutput(current: string, chunk: Buffer | string): string {
  const output = (current + chunk.toString()).slice(-MAX_OUTPUT)
  status = { ...status, output: cleanOutput(output) }

  return output
}

function failed(message: string, error = 'sync-failed', output = ''): DesktopUpstreamSyncResult {
  return { ok: false, state: 'failed', error, message, output: cleanOutput(output) }
}

export function parseUpstreamSyncResult(output: string): DesktopUpstreamSyncResult | null {
  const line = output
    .split(/\r?\n/)
    .reverse()
    .find(candidate => candidate.startsWith(RESULT_PREFIX))

  if (!line) {
    return null
  }

  try {
    const value = JSON.parse(line.slice(RESULT_PREFIX.length)) as DesktopUpstreamSyncResult

    if (
      typeof value?.ok !== 'boolean' ||
      !['completed', 'failed', 'handoff'].includes(value.state) ||
      typeof value.message !== 'string'
    ) {
      return null
    }

    return value
  } catch {
    return null
  }
}

export function resolveUpstreamSyncExit(output: string, code: number | null): DesktopUpstreamSyncResult {
  const result = parseUpstreamSyncResult(output)

  if (code !== 0) {
    if (result && !result.ok) {
      return { ...result, output: cleanOutput(output) }
    }

    return failed(`Hermes upstream sync exited ${code ?? 'without a status'}.`, 'sync-exited', output)
  }

  return result
    ? { ...result, output: cleanOutput(output) }
    : failed('Hermes upstream sync exited successfully without returning a result.', 'missing-result', output)
}

export function updateOperationConflict(
  requested: 'apply' | 'sync',
  state: { syncRunning: boolean; updateRunning: boolean; handoffConflict?: null | { message: string } }
): string | null {
  if (requested === 'apply' && state.syncRunning) {
    return 'Upstream reconciliation is still running. Wait for it to finish before updating Desktop.'
  }

  if (requested === 'sync' && (state.updateRunning || state.handoffConflict)) {
    return (
      state.handoffConflict?.message ||
      'A Desktop update is already running. Wait for it to finish before reconciling upstream.'
    )
  }

  return null
}

export function stopUpstreamSyncChild(
  child: Pick<ChildProcess, 'kill' | 'pid'>,
  options: {
    isWindows?: boolean
    killGroup?: (pid: number) => void
    killTree?: (pid: number) => void
  } = {}
) {
  const pid = child.pid
  const isWindows = options.isWindows ?? process.platform === 'win32'

  if (!Number.isInteger(pid) || !pid || pid <= 0) {
    child.kill()

    return
  }

  try {
    if (isWindows) {
      const killTree =
        options.killTree ??
        (value => execFileSync('taskkill', ['/PID', String(value), '/T', '/F'], { stdio: 'ignore', windowsHide: true }))

      killTree(pid)
    } else {
      ;(options.killGroup ?? (value => process.kill(-value, 'SIGTERM')))(pid)
    }
  } catch {
    child.kill()
  }
}

export function runUpstreamSync(options: {
  python: string
  repo: string
  branch: string
  env?: NodeJS.ProcessEnv
  timeoutMs?: number
}): Promise<DesktopUpstreamSyncResult> {
  if (active) {
    return active
  }

  status = { running: true, startedAt: Date.now() }

  active = new Promise(resolve => {
    let output = ''
    let settled = false
    let timer: NodeJS.Timeout | null = null

    const done = (result: DesktopUpstreamSyncResult) => {
      if (settled) {
        return
      }

      settled = true

      if (timer) {
        clearTimeout(timer)
      }

      active = null
      status = {
        running: false,
        startedAt: status.startedAt,
        output: result.output,
        result: { ...result }
      }
      resolve(result)
    }

    const child = spawn(
      options.python,
      ['-P', '-m', 'hermes_cli.axiom_update', 'sync-upstream', '--repo', options.repo, '--branch', options.branch],
      {
        cwd: options.repo,
        detached: process.platform !== 'win32',
        env: {
          ...process.env,
          ...options.env,
          PYTHONPATH: [options.repo, options.env?.PYTHONPATH, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
          PYTHONUNBUFFERED: '1'
        },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true
      }
    )

    const append = (chunk: Buffer | string) => {
      output = appendUpstreamSyncOutput(output, chunk)
    }

    child.stdout?.on('data', append)
    child.stderr?.on('data', append)
    child.once('error', error => done(failed(error.message, 'spawn-failed', output)))
    child.once('close', code => done(resolveUpstreamSyncExit(output, code)))
    timer = setTimeout(() => {
      stopUpstreamSyncChild(child)
      done(
        failed(
          `Hermes upstream sync exceeded ${Math.ceil((options.timeoutMs ?? DEFAULT_TIMEOUT_MS) / 60_000)} minutes and was stopped.`,
          'sync-timeout',
          output
        )
      )
    }, options.timeoutMs ?? DEFAULT_TIMEOUT_MS)
    timer.unref?.()
  })

  return active
}