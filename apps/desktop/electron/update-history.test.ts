import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  appendUpdateHistory,
  type DesktopUpdateHistoryEntry,
  readUpdateHistory,
  type UpdateHistoryFs
} from './update-history'

function entry(id: number): DesktopUpdateHistoryEntry {
  return {
    id: `entry-${id}`,
    at: id,
    phase: 'apply',
    result: 'completed',
    branch: 'main',
    baseSha: 'a'.repeat(40),
    targetSha: 'b'.repeat(40),
    message: `Update ${id}`
  }
}

function memoryFs(initial: string | null): UpdateHistoryFs & { reads: () => string | null; renames: string[] } {
  let value = initial
  const pending = new Map<string, string>()
  const renames: string[] = []

  return {
    readFile: () => value,
    mkdir: () => {},
    writeFile: (file, data) => pending.set(file, data),
    rename: (from, to) => {
      renames.push(`${from}->${to}`)
      value = pending.get(from) ?? null
    },
    randomToken: () => 'token',
    reads: () => value,
    renames
  }
}

test('history reader tolerates malformed files and malformed entries', () => {
  assert.deepEqual(readUpdateHistory('/history.json', { readFile: () => '{' }), [])
  assert.deepEqual(
    readUpdateHistory('/history.json', {
      readFile: () => JSON.stringify([entry(2), null, { result: 'completed' }, entry(1)])
    }),
    [entry(2), entry(1)]
  )
})

test('history reader returns newest first and caps at 50', () => {
  const values = Array.from({ length: 60 }, (_, index) => entry(index + 1)).reverse()
  const result = readUpdateHistory('/history.json', { readFile: () => JSON.stringify(values) })

  assert.equal(result.length, 50)
  assert.equal(result[0]?.id, 'entry-60')
  assert.equal(result[49]?.id, 'entry-11')
})

test('append is atomic, newest-first, de-duplicates ids, and retains 50', () => {
  const fs = memoryFs(JSON.stringify(Array.from({ length: 50 }, (_, index) => entry(50 - index))))
  const result = appendUpdateHistory('/logs/update-history.json', entry(51), fs)

  assert.equal(result.length, 50)
  assert.equal(result[0]?.id, 'entry-51')
  assert.equal(result.at(-1)?.id, 'entry-2')
  assert.deepEqual(fs.renames, ['/logs/update-history.json.token.tmp->/logs/update-history.json'])
  assert.deepEqual(JSON.parse(fs.reads() ?? '[]'), result)

  const replaced = appendUpdateHistory('/logs/update-history.json', { ...entry(40), message: 'retry' }, fs)
  assert.equal(replaced.filter(value => value.id === 'entry-40').length, 1)
  assert.equal(replaced[0]?.message, 'retry')
})
