import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, test } from 'vitest'

import { createStagedUpdateLifecycle } from './staged-update-lifecycle'

const roots: string[] = []

afterEach(() => {
  for (const root of roots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

function tempRoot(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-stage-lifecycle-'))
  roots.push(root)
  return root
}

function lifecycle(overrides: Record<string, unknown> = {}) {
  const hermesHome = tempRoot()
  const updateRoot = path.join(hermesHome, 'repo')
  fs.mkdirSync(updateRoot, { recursive: true })

  return {
    hermesHome,
    updateRoot,
    lifecycle: createStagedUpdateLifecycle({
      hermesHome,
      isWindows: true,
      resolveUpdateRoot: () => updateRoot,
      readDesktopUpdateConfig: () => ({ branch: 'axiom' }),
      checkUpdates: async () => ({ supported: true, behind: 0 }),
      runGit: async () => ({ code: 0, stdout: '', stderr: '' }),
      pathWithHermesManagedNode: value => value,
      rememberLog: () => {},
      spawnStageWorker: () => ({ pid: 42, unref: () => {} }),
      readWindowsProcessCommandLine: () => '',
      forceKillProcessTree: () => {},
      applyUpdates: async () => ({ ok: true }),
      sleep: async () => {},
      ...overrides
    })
  }
}

test('reports an idle stage when no worker state or manifest exists', async () => {
  const subject = lifecycle()

  assert.deepEqual(await subject.lifecycle.getStatus(), {
    supported: true,
    phase: 'idle',
    reason: 'missing'
  })
})

test('renderer status does not expose a progress log path that was not manifest-validated', async () => {
  const subject = lifecycle()
  const logPath = path.join(subject.hermesHome, 'logs', 'desktop-update-stage.log')
  fs.mkdirSync(path.dirname(logPath), { recursive: true })
  fs.writeFileSync(logPath, 'first line\nsecond line\n')
  fs.mkdirSync(path.join(subject.hermesHome, 'update-stage', 'desktop'), { recursive: true })
  fs.writeFileSync(
    path.join(subject.hermesHome, 'update-stage', 'desktop', 'progress.json'),
    JSON.stringify({ phase: 'building', message: 'Building', logPath, updatedAt: 10 })
  )

  const status = await subject.lifecycle.getRendererStatus()

  assert.equal(status.phase, 'failed')
  assert.equal(status.output, undefined)
  assert.equal(status.ownerActive, false)
  assert.equal(status.cancellable, false)
})

test('cancellation refuses to terminate a process whose command line does not own the Hermes stage', async () => {
  const commandLines: number[] = []
  const killed: number[] = []
  const subject = lifecycle({
    processAlive: () => true,
    readWindowsProcessCommandLine: (pid: number) => {
      commandLines.push(pid)
      return 'powershell -File C:\\other\\worker.ps1'
    },
    forceKillProcessTree: (pid: number) => killed.push(pid)
  })
  const lock = path.join(subject.hermesHome, 'update-stage', 'desktop', '.prepare-lock')
  fs.mkdirSync(lock, { recursive: true })
  fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: 73, token: 'owner-token' }))

  assert.deepEqual(await subject.lifecycle.cancelPreparation(), {
    ok: false,
    cancelled: false,
    error: 'identity-mismatch',
    message: 'The stage owner no longer matches the Hermes preparation worker. Nothing was terminated.'
  })
  assert.deepEqual(commandLines, [73])
  assert.deepEqual(killed, [])
})

test('discard imports terminal history before removing the transient stage', async () => {
  const subject = lifecycle()
  const stageRoot = path.join(subject.hermesHome, 'update-stage', 'desktop')
  fs.mkdirSync(stageRoot, { recursive: true })
  fs.writeFileSync(
    path.join(stageRoot, 'stage-result.json'),
    JSON.stringify({ ok: false, phase: 'cancelled', cancelled: true, finishedAt: 123, targetSha: 'a'.repeat(40) })
  )

  assert.deepEqual(await subject.lifecycle.discard(), { ok: true, discarded: true })
  assert.equal(fs.existsSync(stageRoot), false)

  const history = JSON.parse(fs.readFileSync(path.join(subject.hermesHome, 'logs', 'update-history.json'), 'utf8'))
  assert.equal(history[0].result, 'cancelled')
  assert.equal(history[0].phase, 'prepare')
})
