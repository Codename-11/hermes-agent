import assert from 'node:assert/strict'

import { describe, test } from 'vitest'

import {
  type DesktopUpdateStageManifest,
  parseUpdateStageManifest,
  readUpdateStageManifest,
  validateUpdateStageManifest,
  writeUpdateStageManifestAtomic
} from './update-stage'

const BASE = 'a'.repeat(40)
const TARGET = 'b'.repeat(40)
const HASH = 'c'.repeat(64)
const TREE_HASH = 'e'.repeat(64)

function manifest(overrides: Partial<DesktopUpdateStageManifest> = {}): DesktopUpdateStageManifest {
  return {
    schemaVersion: 1,
    branch: 'main',
    baseSha: BASE,
    targetSha: TARGET,
    installRoot: '/opt/hermes',
    artifactPath: '/home/hermes/.hermes/update-stage/desktop/worktree/apps/desktop/release/win-unpacked/Hermes.exe',
    artifactDir: '/home/hermes/.hermes/update-stage/desktop/worktree/apps/desktop/release/win-unpacked',
    artifactSha256: HASH,
    artifactTreeSha256: TREE_HASH,
    buildStampPath: '/home/hermes/.hermes/update-stage/desktop/desktop-build-stamp.json',
    worktree: '/home/hermes/.hermes/update-stage/desktop/worktree',
    liveDirtyFingerprint: 'd'.repeat(64),
    logPath: '/home/hermes/.hermes/logs/desktop-update-stage.log',
    createdAt: 1_786_406_400_000,
    ...overrides
  }
}

const live = {
  branch: 'main',
  headSha: BASE,
  installRoot: '/opt/hermes',
  targetSha: TARGET,
  stageRoot: '/home/hermes/.hermes/update-stage/desktop',
  dirtyFingerprint: 'd'.repeat(64)
}

describe('stage manifest parsing', () => {
  test('accepts the exact typed manifest contract', () => {
    assert.deepEqual(parseUpdateStageManifest(JSON.stringify(manifest())), manifest())
  })

  test('rejects malformed JSON and malformed hashes', () => {
    assert.throws(() => parseUpdateStageManifest('{'), /malformed/i)
    assert.throws(() => parseUpdateStageManifest(JSON.stringify(manifest({ artifactSha256: 'not-a-hash' }))), /hash/i)
  })
})

describe('stage manifest validation', () => {
  test('accepts exact branch/base/target/install root and artifact hash', () => {
    assert.deepEqual(
      validateUpdateStageManifest(manifest(), live, {
        fileExists: () => true,
        sha256File: () => HASH,
        sha256Tree: () => TREE_HASH
      }),
      { valid: true, manifest: manifest() }
    )
  })

  test.each([
    ['target moved', manifest(), { ...live, targetSha: 'd'.repeat(40) }, 'target-changed'],
    ['live HEAD changed', manifest(), { ...live, headSha: 'd'.repeat(40) }, 'head-changed'],
    ['live branch changed', manifest(), { ...live, branch: 'release' }, 'branch-changed'],
    ['install root changed', manifest(), { ...live, installRoot: '/srv/hermes' }, 'install-root-changed']
  ])('invalidates when %s', (_label, value, context, reason) => {
    assert.deepEqual(
      validateUpdateStageManifest(value as DesktopUpdateStageManifest, context, {
        fileExists: () => true,
        sha256File: () => HASH,
        sha256Tree: () => TREE_HASH
      }),
      { valid: false, reason }
    )
  })

  test('invalidates a missing artifact', () => {
    assert.deepEqual(
      validateUpdateStageManifest(manifest(), live, {
        fileExists: () => false,
        sha256File: () => HASH,
        sha256Tree: () => TREE_HASH
      }),
      {
        valid: false,
        reason: 'missing-artifact'
      }
    )
  })

  test('invalidates an artifact whose content hash changed', () => {
    assert.deepEqual(
      validateUpdateStageManifest(manifest(), live, {
        fileExists: () => true,
        sha256File: () => 'd'.repeat(64)
      }),
      { valid: false, reason: 'artifact-hash-mismatch' }
    )
  })

  test('invalidates a changed package tree even when Hermes.exe is unchanged', () => {
    assert.deepEqual(
      validateUpdateStageManifest(manifest(), live, {
        fileExists: () => true,
        sha256File: () => HASH,
        sha256Tree: () => 'f'.repeat(64)
      }),
      { valid: false, reason: 'artifact-hash-mismatch' }
    )
  })

  test('invalidates changed live dirt and paths outside the owned stage root', () => {
    assert.deepEqual(
      validateUpdateStageManifest(manifest(), { ...live, dirtyFingerprint: 'e'.repeat(64) }, {
        fileExists: () => true,
        sha256File: () => HASH,
        sha256Tree: () => TREE_HASH
      }),
      { valid: false, reason: 'dirty-state-changed' }
    )
    assert.deepEqual(
      validateUpdateStageManifest(manifest({ artifactPath: '/tmp/Hermes.exe' }), live, {
        fileExists: () => true,
        sha256File: () => HASH,
        sha256Tree: () => TREE_HASH
      }),
      { valid: false, reason: 'stage-path-invalid' }
    )
  })

  test('compares Windows paths case-insensitively', () => {
    const windowsManifest = manifest({
      installRoot: String.raw`C:\Hermes\hermes-agent`,
      artifactPath: String.raw`C:\Users\Bailey\.hermes\update-stage\desktop\worktree\apps\desktop\release\win-unpacked\Hermes.exe`,
      artifactDir: String.raw`C:\Users\Bailey\.hermes\update-stage\desktop\worktree\apps\desktop\release\win-unpacked`,
      buildStampPath: String.raw`C:\Users\Bailey\.hermes\update-stage\desktop\desktop-build-stamp.json`,
      worktree: String.raw`C:\Users\Bailey\.hermes\update-stage\desktop\worktree`
    })

    const result = validateUpdateStageManifest(
      windowsManifest,
      {
        ...live,
        installRoot: String.raw`c:\hermes\HERMES-AGENT`,
        stageRoot: String.raw`c:\users\bailey\.hermes\UPDATE-STAGE\desktop`
      },
      {
        fileExists: () => true,
        sha256File: () => HASH,
        sha256Tree: () => TREE_HASH
      }
    )

    assert.equal(result.valid, true)
  })
})

test('read returns missing/malformed status without throwing', () => {
  assert.deepEqual(readUpdateStageManifest('/stage.json', { readFile: () => null }), { kind: 'missing' })
  assert.deepEqual(readUpdateStageManifest('/stage.json', { readFile: () => '{' }), {
    kind: 'malformed',
    error: 'Malformed update stage manifest JSON'
  })
})

test('atomic writer publishes a temp file before rename', () => {
  const calls: string[] = []
  writeUpdateStageManifestAtomic('/stage/stage.json', manifest(), {
    mkdir: dir => calls.push(`mkdir:${dir}`),
    writeFile: (file, data) => calls.push(`write:${file}:${JSON.parse(data).targetSha}`),
    rename: (from, to) => calls.push(`rename:${from}:${to}`),
    randomToken: () => 'token'
  })

  assert.deepEqual(calls, [
    'mkdir:/stage',
    `write:/stage/stage.json.token.tmp:${TARGET}`,
    'rename:/stage/stage.json.token.tmp:/stage/stage.json'
  ])
})
