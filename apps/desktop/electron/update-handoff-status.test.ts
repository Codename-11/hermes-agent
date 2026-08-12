import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import { hasRetainedDeployHandoff } from './update-handoff-status'

test('recognizes only a handoff owned by the selected repository and branch', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-handoff-status-'))
  const home = path.join(root, 'home')
  const repo = path.join(root, 'repo')

  fs.mkdirSync(home)
  fs.mkdirSync(repo)
  fs.writeFileSync(
    path.join(home, '.update_handoff.json'),
    JSON.stringify({ repo, branch: 'axiom', worktree: path.join(root, 'worktree') })
  )

  assert.equal(hasRetainedDeployHandoff({ branch: 'axiom', hermesHome: home, repo }), true)
  assert.equal(hasRetainedDeployHandoff({ branch: 'tgi', hermesHome: home, repo }), false)
  assert.equal(hasRetainedDeployHandoff({ branch: 'axiom', hermesHome: home, repo: path.join(root, 'other') }), false)

  fs.rmSync(root, { recursive: true, force: true })
})

test('treats missing or malformed markers as no retained handoff', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-handoff-status-'))

  assert.equal(hasRetainedDeployHandoff({ branch: 'axiom', hermesHome: root, repo: root }), false)
  fs.writeFileSync(path.join(root, '.update_handoff.json'), '{')
  assert.equal(hasRetainedDeployHandoff({ branch: 'axiom', hermesHome: root, repo: root }), false)

  fs.rmSync(root, { recursive: true, force: true })
})
