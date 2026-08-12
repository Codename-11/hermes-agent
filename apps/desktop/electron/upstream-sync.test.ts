import assert from 'node:assert/strict'

import { describe, test } from 'vitest'

import { parseUpstreamSyncResult, resolveUpstreamSyncExit, stopUpstreamSyncChild } from './upstream-sync'

describe('upstream sync result parsing', () => {
  test('reads the final typed result after normal progress output', () => {
    assert.deepEqual(
      parseUpstreamSyncResult(
        '→ Fetching upstream…\nHERMES_UPSTREAM_SYNC_RESULT={"ok":true,"state":"completed","message":"Published.","reconciled":2}\n'
      ),
      { ok: true, state: 'completed', message: 'Published.', reconciled: 2 }
    )
  })

  test('rejects missing, malformed, and untyped result records', () => {
    assert.equal(parseUpstreamSyncResult('normal git output only'), null)
    assert.equal(parseUpstreamSyncResult('HERMES_UPSTREAM_SYNC_RESULT={'), null)
    assert.equal(
      parseUpstreamSyncResult('HERMES_UPSTREAM_SYNC_RESULT={"ok":true,"state":"unknown","message":"x"}'),
      null
    )
  })
})

describe('upstream sync process outcomes', () => {
  test('rejects a success payload when the process later exits unsuccessfully', () => {
    const output = 'HERMES_UPSTREAM_SYNC_RESULT={"ok":true,"state":"completed","message":"Published."}\n'

    assert.deepEqual(resolveUpstreamSyncExit(output, 1), {
      ok: false,
      state: 'failed',
      error: 'sync-exited',
      message: 'Hermes upstream sync exited 1.'
    })
  })

  test('requires a structured result even after exit zero', () => {
    assert.deepEqual(resolveUpstreamSyncExit('normal output', 0), {
      ok: false,
      state: 'failed',
      error: 'missing-result',
      message: 'Hermes upstream sync exited successfully without returning a result.'
    })
  })

  test('terminates the full Windows process tree when a timed operation has a pid', () => {
    const directKills: Array<string | number | undefined> = []
    const treeKills: number[] = []

    stopUpstreamSyncChild(
      { pid: 4242, kill: signal => (directKills.push(signal), true) },
      { isWindows: true, killTree: pid => treeKills.push(pid) }
    )

    assert.deepEqual(treeKills, [4242])
    assert.deepEqual(directKills, [])
  })

  test('signals the detached POSIX process group and falls back to the child if that fails', () => {
    const directKills: Array<string | number | undefined> = []
    const groups: number[] = []
    const child = { pid: 3131, kill: signal => (directKills.push(signal), true) }

    stopUpstreamSyncChild(child, { isWindows: false, killGroup: pid => groups.push(pid) })
    stopUpstreamSyncChild(child, {
      isWindows: false,
      killGroup: () => {
        throw new Error('gone')
      }
    })

    assert.deepEqual(groups, [3131])
    assert.deepEqual(directKills, [undefined])
  })
})
