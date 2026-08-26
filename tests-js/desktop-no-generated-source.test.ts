/** Desktop sources must not contain tracked TypeScript emit beside TypeScript. */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

import { test } from 'vitest'

const SOURCE_ROOT = path.resolve(__dirname, '..', 'apps', 'desktop', 'src')

function walk(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const child = path.join(directory, entry.name)
    return entry.isDirectory() ? walk(child) : [child]
  })
}

test('desktop source has no generated .js sibling beside .ts/.tsx', () => {
  const files = new Set(walk(SOURCE_ROOT))
  const generated = [...files]
    .filter((file) => file.endsWith('.js'))
    .filter((file) => files.has(file.slice(0, -3) + '.ts') || files.has(file.slice(0, -3) + '.tsx'))
    .map((file) => path.relative(SOURCE_ROOT, file))
    .sort()

  assert.deepEqual(
    generated,
    [],
    'TypeScript clean deletes colocated emit, dirtying managed checkouts after every Desktop build'
  )
})