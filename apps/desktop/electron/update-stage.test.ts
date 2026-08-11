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

function manifest(overrides: Partial<DesktopUpdateStageManifest> = {}): DesktopUpdateStageManifest {
  return {
    schemaVersion: 1,
    branch: 'main',
    baseSha: BASE,
    targetSha: TARGET,
    installRoot: '/opt/hermes',
    artifactPath: '/home/hermes/.hermes/update-stage/desktop/release/Hermes.exe',
    artifactSha256: HASH,
    createdAt: 1_786_406_400_000,
    ...overrides
  }
}

const live = {
  branch: 'main',
  headSha: BASE,
  installRoot: '/opt/hermes',
  targetSha: TARGET
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
        sha256File: () => HASH
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
        sha256File: () => HASH
      }),
      { valid: false, reason }
    )
  })

  test('invalidates a missing artifact', () => {
    assert.deepEqual(
      validateUpdateStageManifest(manifest(), live, { fileExists: () => false, sha256File: () => HASH }),
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
