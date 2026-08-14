import { execFileSync, type SpawnOptions } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

import { appendUpdateHistory, readUpdateHistory } from './update-history'
import {
  type DesktopUpdateStageManifest,
  readUpdateStageManifest,
  reconcileUpdateStageProgress,
  validateUpdateStageManifest
} from './update-stage'
import {
  cancelledStageProgress,
  cancelledStageResult,
  commandOwnsStagePreparation,
  parseStagePreparationOwner,
  type StagePreparationOwner
} from './update-stage-cancel'
import {
  resolveStageUpdateScript,
  spawnUpdaterProcess,
  type UpdaterChild,
  wrapHandoffForDetachedConsole
} from './updater-process'
import { hiddenWindowsChildOptions } from './windows-child-options'

interface GitResult {
  code: number
  stdout: string
  stderr: string
}

interface UpdateCheck {
  supported?: boolean
  behind?: number
  currentSha?: string
  targetSha?: string
  branch?: string
  currentBranch?: string
  error?: string
  message?: string
}

export interface StagedUpdateLifecycleDeps {
  hermesHome: string
  isWindows: boolean
  resolveUpdateRoot: () => string
  readDesktopUpdateConfig: () => { branch?: string }
  checkUpdates: () => Promise<UpdateCheck>
  runGit: (args: string[], options?: { cwd?: string }) => Promise<GitResult>
  pathWithHermesManagedNode: (venvBin: string) => string
  rememberLog: (message: string) => void
  readWindowsProcessCommandLine?: (pid: number) => string
  forceKillProcessTree: (pid: number) => void
  applyUpdates: (options: { stageManifest: string }) => Promise<Record<string, unknown>>
  writeFileAtomic?: (filePath: string, data: string, encoding?: BufferEncoding) => void
  spawnStageWorker?: (command: string, args: string[], options: SpawnOptions) => UpdaterChild
  processAlive?: (pid: number) => boolean
  sleep?: (milliseconds: number) => Promise<void>
  now?: () => number
}

export interface StagedUpdateLifecycle {
  cancelPreparation: () => Promise<Record<string, unknown>>
  discard: () => Promise<Record<string, unknown>>
  getHistory: () => unknown[]
  getRendererStatus: (options?: { refreshTarget?: boolean }) => Promise<Record<string, any>>
  getStatus: (options?: { refreshTarget?: boolean }) => Promise<Record<string, any>>
  prepare: () => Promise<Record<string, unknown>>
  preparationIsRunning: () => boolean
  restartAndApply: () => Promise<Record<string, unknown>>
}

const ACTIVE_RENDERER_PHASES = new Set(['fetching', 'preparing-dependencies', 'building', 'verifying'])

/** Owns the staged-update filesystem/process lifecycle; main.ts supplies only app wiring. */
export function createStagedUpdateLifecycle(deps: StagedUpdateLifecycleDeps): StagedUpdateLifecycle {
  const stageRoot = () => path.join(deps.hermesHome, 'update-stage', 'desktop')
  const manifestPath = () => path.join(stageRoot(), 'stage.json')
  const historyPath = () => path.join(deps.hermesHome, 'logs', 'update-history.json')
  const now = deps.now ?? Date.now
  const sleep = deps.sleep ?? (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)))
  const writeAtomic =
    deps.writeFileAtomic ??
    ((filePath: string, data: string, encoding: BufferEncoding = 'utf8') => fs.writeFileSync(filePath, data, encoding))
  const processAlive =
    deps.processAlive ??
    ((pid: number) => {
      try {
        process.kill(pid, 0)
        return true
      } catch {
        return false
      }
    })
  const spawnStageWorker =
    deps.spawnStageWorker ??
    ((command: string, args: string[], options: SpawnOptions) => spawnUpdaterProcess(command, args, options))
  const readWindowsProcessCommandLine =
    deps.readWindowsProcessCommandLine ??
    ((pid: number) => {
      const query =
        `$value = Get-CimInstance Win32_Process -Filter "ProcessId = ${pid}" -ErrorAction Stop; ` +
        'if ($null -ne $value) { [Console]::Out.Write($value.CommandLine) }'

      return execFileSync(
        'powershell',
        ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', query],
        hiddenWindowsChildOptions({ encoding: 'utf8', timeout: 5_000, stdio: ['ignore', 'pipe', 'ignore'] })
      ).trim()
    })

  function readHistory() {
    try {
      const result = JSON.parse(fs.readFileSync(path.join(stageRoot(), 'stage-result.json'), 'utf8'))
      const finishedAt = Number(result?.finishedAt)

      if (result?.ok === false && Number.isFinite(finishedAt)) {
        appendUpdateHistory(historyPath(), {
          id: `prepare-${finishedAt}-${String(result.targetSha || 'unknown').slice(0, 10)}`,
          at: finishedAt,
          phase: 'prepare',
          result: result.cancelled === true || result.phase === 'cancelled' ? 'cancelled' : 'failed',
          targetSha: typeof result.targetSha === 'string' ? result.targetSha : undefined,
          message: typeof result.message === 'string' ? result.message : 'Update preparation failed.',
          logPath: path.join(deps.hermesHome, 'logs', 'desktop-update-stage.log')
        })
      }
    } catch {
      // No preparation result yet, or an interrupted writer.
    }

    return readUpdateHistory(historyPath())
  }

  function readProgress() {
    try {
      const value = JSON.parse(fs.readFileSync(path.join(stageRoot(), 'progress.json'), 'utf8'))
      const phases: Record<string, string> = {
        fetching: 'fetching',
        worktree: 'preparing-dependencies',
        dependencies: 'preparing-dependencies',
        'preparing-dependencies': 'preparing-dependencies',
        building: 'building',
        verifying: 'verifying',
        ready: 'ready',
        failed: 'failed'
      }
      const phase = phases[String(value?.phase || '')] || 'failed'

      return {
        phase,
        message: typeof value?.message === 'string' ? value.message : undefined,
        percent: Number.isFinite(value?.percent) ? value.percent : undefined,
        updatedAt: Number.isFinite(value?.updatedAt) ? value.updatedAt : undefined
      } as any
    } catch {
      return null
    }
  }

  function readOwner(): StagePreparationOwner | null {
    try {
      return parseStagePreparationOwner(
        JSON.parse(fs.readFileSync(path.join(stageRoot(), '.prepare-lock', 'owner.json'), 'utf8'))
      )
    } catch {
      return null
    }
  }

  function preparationIsRunning(): boolean {
    const owner = readOwner()
    return owner ? processAlive(owner.pid) : false
  }

  function installCompletedAfter(timestamp: number | undefined): boolean {
    if (!Number.isFinite(timestamp)) {
      return false
    }

    try {
      const stamp = JSON.parse(
        fs.readFileSync(path.join(deps.resolveUpdateRoot(), 'apps', 'desktop', 'build', 'install-stamp.json'), 'utf8')
      )
      const builtAt = Date.parse(stamp?.builtAt)
      return Number.isFinite(builtAt) && builtAt > (timestamp as number)
    } catch {
      return false
    }
  }

  async function waitForOwnership(timeoutMs = 10_000): Promise<boolean> {
    const deadline = now() + timeoutMs

    while (now() < deadline) {
      if (preparationIsRunning()) {
        return true
      }
      await sleep(100)
    }

    return false
  }

  async function dirtyContentFingerprint(updateRoot: string): Promise<string> {
    const [tracked, untracked, summary] = await Promise.all([
      deps.runGit(['-c', 'core.quotepath=false', 'diff', '--name-only', 'HEAD', '--'], { cwd: updateRoot }),
      deps.runGit(['-c', 'core.quotepath=false', 'ls-files', '--others', '--exclude-standard'], { cwd: updateRoot }),
      deps.runGit(['-c', 'core.quotepath=false', 'diff', '--summary', 'HEAD', '--'], { cwd: updateRoot })
    ])

    if ([tracked, untracked, summary].some(result => result.code !== 0)) {
      throw new Error('Could not fingerprint the live checkout changes.')
    }

    const paths = [...new Set(`${tracked.stdout}\n${untracked.stdout}`.split(/\r?\n/).filter(Boolean))].sort()
    const records: string[] = []

    for (const relative of paths) {
      let exists = true
      try {
        fs.lstatSync(path.join(updateRoot, relative))
      } catch {
        exists = false
      }

      if (!exists) {
        records.push(`${relative}\0deleted`)
        continue
      }

      const blob = await deps.runGit(['hash-object', '--no-filters', '--', relative], { cwd: updateRoot })
      if (blob.code !== 0) {
        throw new Error(`Could not fingerprint dirty path: ${relative}`)
      }
      records.push(`${relative}\0${blob.stdout.trim()}`)
    }

    const normalizedSummary = summary.stdout.replace(/\r\n/g, '\n').trim()
    return crypto.createHash('sha256').update(`${normalizedSummary}\n${records.join('\n')}`, 'utf8').digest('hex')
  }

  async function liveState(manifest: DesktopUpdateStageManifest, options: { refreshTarget?: boolean } = {}) {
    const updateRoot = deps.resolveUpdateRoot()

    if (options.refreshTarget) {
      const fetched = await deps.runGit(['fetch', 'origin', manifest.branch, '--prune'], { cwd: updateRoot })
      if (fetched.code !== 0) {
        throw new Error(`Could not refresh origin/${manifest.branch}. Desktop was not closed.`)
      }
    }

    const [branch, head, target, dirtyFingerprint] = await Promise.all([
      deps.runGit(['rev-parse', '--abbrev-ref', 'HEAD'], { cwd: updateRoot }),
      deps.runGit(['rev-parse', 'HEAD'], { cwd: updateRoot }),
      deps.runGit(['rev-parse', `refs/remotes/origin/${manifest.branch}`], { cwd: updateRoot }),
      dirtyContentFingerprint(updateRoot)
    ])

    if ([branch, head, target].some(result => result.code !== 0)) {
      throw new Error('Could not validate the prepared update against the live checkout.')
    }

    return {
      branch: branch.stdout.trim(),
      headSha: head.stdout.trim(),
      installRoot: updateRoot,
      targetSha: target.stdout.trim(),
      stageRoot: stageRoot(),
      dirtyFingerprint
    }
  }

  async function getStatus(options: { refreshTarget?: boolean } = {}): Promise<Record<string, any>> {
    if (!deps.isWindows) {
      return { supported: false, phase: 'idle', message: 'Staged updates currently require Windows.' }
    }

    const read = readUpdateStageManifest(manifestPath())

    if (read.kind === 'malformed') {
      return { supported: true, phase: 'invalidated', reason: 'malformed', message: read.error }
    }

    if (read.kind === 'ready') {
      try {
        const live = await liveState(read.manifest, options)
        const validation = validateUpdateStageManifest(read.manifest, live)

        if (validation.valid === false) {
          return {
            supported: true,
            phase: 'invalidated',
            reason: validation.reason,
            manifest: read.manifest,
            message: 'The prepared update no longer matches the live installation. Prepare it again.'
          }
        }

        return {
          supported: true,
          phase: 'ready',
          percent: 100,
          message: `Ready to restart into ${read.manifest.targetSha.slice(0, 10)}.`,
          logPath: read.manifest.logPath,
          manifest: read.manifest
        }
      } catch (error: any) {
        return {
          supported: true,
          phase: 'invalidated',
          reason: 'target-changed',
          manifest: read.manifest,
          message: error?.message || String(error)
        }
      }
    }

    const progress = readProgress()
    if (progress) {
      const reconciled = reconcileUpdateStageProgress(progress, {
        ownerAlive: preparationIsRunning(),
        installCompletedAfterProgress: installCompletedAfter(progress.updatedAt)
      })

      if (reconciled.phase === 'failed') {
        readHistory()
      }

      if (reconciled.phase === 'ready') {
        return {
          supported: true,
          phase: 'invalidated',
          reason: 'missing',
          message: 'Preparation reported ready, but its manifest is missing. Prepare the update again.'
        }
      }

      return { supported: true, ...reconciled }
    }

    return { supported: true, phase: 'idle', reason: 'missing' }
  }

  async function getRendererStatus(options: { refreshTarget?: boolean } = {}) {
    const status = await getStatus(options)
    const ownerActive = preparationIsRunning()
    const expectedLogPath = path.join(deps.hermesHome, 'logs', 'desktop-update-stage.log')
    let output: string | undefined

    if (status.logPath === expectedLogPath) {
      try {
        output = fs.readFileSync(expectedLogPath, 'utf8').slice(-24_000).trim() || undefined
      } catch {
        // The worker may not have created the log yet.
      }
    }

    return {
      ...status,
      output,
      checkedAt: now(),
      ownerActive,
      cancellable: ownerActive && ACTIVE_RENDERER_PHASES.has(status.phase)
    }
  }

  async function discard() {
    if (preparationIsRunning()) {
      return { ok: false, discarded: false, error: 'preparation-running' }
    }

    readHistory()
    const root = stageRoot()
    const worktree = path.join(root, 'worktree')
    const updateRoot = deps.resolveUpdateRoot()

    if (fs.existsSync(worktree)) {
      await deps.runGit(['worktree', 'remove', '--force', worktree], { cwd: updateRoot }).catch(() => null)
      await deps.runGit(['worktree', 'prune'], { cwd: updateRoot }).catch(() => null)
    }

    const existed = fs.existsSync(root)
    fs.rmSync(root, { recursive: true, force: true })
    return { ok: true, discarded: existed }
  }

  async function prepare() {
    if (!deps.isWindows) {
      return {
        ok: false,
        error: 'unsupported',
        status: { supported: false, phase: 'idle', message: 'Staged updates currently require Windows.' }
      }
    }

    const currentStage = await getStatus()
    if (preparationIsRunning() || currentStage.phase === 'ready') {
      return { ok: true, status: currentStage }
    }

    const update = await deps.checkUpdates()
    if (!update.supported || !update.currentSha || !update.targetSha || (update.behind ?? 0) <= 0) {
      return {
        ok: false,
        error: update.error || 'no-update',
        status: { supported: true, phase: 'failed', message: update.message || 'No client update is available.' }
      }
    }

    if (currentStage.phase !== 'idle') {
      await discard()
    }

    const updateRoot = deps.resolveUpdateRoot()
    const branch = update.branch || update.currentBranch || deps.readDesktopUpdateConfig().branch || 'main'
    const recipe = resolveStageUpdateScript(updateRoot, {
      installRoot: updateRoot,
      branch,
      baseSha: update.currentSha,
      targetSha: update.targetSha,
      stageRoot: stageRoot()
    })

    if (!recipe) {
      return {
        ok: false,
        error: 'stage-script-missing',
        status: { supported: false, phase: 'failed', message: 'This checkout does not include staged update support.' }
      }
    }

    const venvBin = path.join(updateRoot, 'venv', 'Scripts')
    const detachedRecipe = wrapHandoffForDetachedConsole(recipe, [])
    const child = spawnStageWorker(detachedRecipe.command, detachedRecipe.args, {
      cwd: deps.hermesHome,
      env: { ...process.env, HERMES_HOME: deps.hermesHome, PATH: deps.pathWithHermesManagedNode(venvBin) },
      detached: true,
      stdio: 'ignore'
    })

    child.unref()
    deps.rememberLog(
      `[updates] preparing ${update.targetSha.slice(0, 10)} in isolated worktree (pid ${child.pid || 'unknown'})`
    )

    if (!(await waitForOwnership())) {
      return {
        ok: false,
        error: 'preparation-not-started',
        status: {
          supported: true,
          phase: 'failed',
          message: 'The detached preparation worker did not claim its stage lock. Nothing was prepared.'
        }
      }
    }

    return {
      ok: true,
      status: {
        supported: true,
        phase: 'fetching',
        percent: 0,
        message: 'Preparing update while Desktop remains available.',
        checkedAt: now(),
        ownerActive: true,
        cancellable: true
      }
    }
  }

  async function cancelPreparation() {
    if (!deps.isWindows) {
      return { ok: false, cancelled: false, error: 'unsupported' }
    }

    const owner = readOwner()
    if (!owner || !preparationIsRunning()) {
      return { ok: false, cancelled: false, error: 'not-running', message: 'No active preparation worker was found.' }
    }

    const updateRoot = deps.resolveUpdateRoot()
    const scriptPath = path.join(updateRoot, 'scripts', 'desktop-stage-update.ps1')
    let commandLine = ''

    try {
      commandLine = readWindowsProcessCommandLine(owner.pid)
    } catch (error: any) {
      return {
        ok: false,
        cancelled: false,
        error: 'identity-unavailable',
        message: `Could not verify the preparation worker before cancellation: ${error?.message || String(error)}`
      }
    }

    if (!commandOwnsStagePreparation(commandLine, scriptPath, stageRoot())) {
      return {
        ok: false,
        cancelled: false,
        error: 'identity-mismatch',
        message: 'The stage owner no longer matches the Hermes preparation worker. Nothing was terminated.'
      }
    }

    deps.forceKillProcessTree(owner.pid)
    const deadline = now() + 5_000
    while (now() < deadline && preparationIsRunning()) {
      await sleep(100)
    }

    if (preparationIsRunning()) {
      return {
        ok: false,
        cancelled: false,
        error: 'worker-still-running',
        message: 'The preparation worker did not stop. Its stage was left untouched.'
      }
    }

    const currentOwner = readOwner()
    if (currentOwner && (currentOwner.token !== owner.token || currentOwner.pid !== owner.pid)) {
      return {
        ok: false,
        cancelled: false,
        error: 'ownership-changed',
        message: 'Preparation ownership changed while cancellation was in progress. Stage files were left untouched.'
      }
    }

    const finishedAt = now()
    fs.mkdirSync(stageRoot(), { recursive: true })
    writeAtomic(path.join(stageRoot(), 'progress.json'), JSON.stringify(cancelledStageProgress(finishedAt), null, 2), 'utf8')
    writeAtomic(
      path.join(stageRoot(), 'stage-result.json'),
      JSON.stringify(cancelledStageResult(owner.targetSha, finishedAt), null, 2),
      'utf8'
    )
    fs.rmSync(path.join(stageRoot(), '.prepare-lock'), { recursive: true, force: true })
    readHistory()

    return { ok: true, cancelled: true, status: await getStatus() }
  }

  async function restartAndApply() {
    const status = await getStatus({ refreshTarget: true })
    if (status.phase !== 'ready' || !status.manifest) {
      return { ok: false, applying: false, error: status.reason || 'stage-not-ready', message: status.message }
    }

    const result = await deps.applyUpdates({ stageManifest: manifestPath() })
    return { ...result, applying: result.ok === true }
  }

  return {
    cancelPreparation,
    discard,
    getHistory: readHistory,
    getRendererStatus,
    getStatus,
    prepare,
    preparationIsRunning,
    restartAndApply
  }
}
