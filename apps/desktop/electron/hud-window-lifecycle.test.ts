import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'

import { test } from 'vitest'

import { bindHudCloseBehavior, suppressHudCloseBehavior } from './hud-window-lifecycle'

test('suppressing HUD restore behavior preserves resource cleanup listeners', () => {
  const win = new EventEmitter()
  let cleanupCalls = 0
  let behaviorCalls = 0

  win.once('closed', () => {
    cleanupCalls += 1
  })
  bindHudCloseBehavior(win, () => {
    behaviorCalls += 1
  })

  suppressHudCloseBehavior(win)
  win.emit('closed')

  assert.equal(cleanupCalls, 1)
  assert.equal(behaviorCalls, 0)
})

test('rebinding replaces only the prior HUD behavior listener', () => {
  const win = new EventEmitter()
  const calls: string[] = []

  bindHudCloseBehavior(win, () => calls.push('old'))
  bindHudCloseBehavior(win, () => calls.push('new'))
  win.emit('closed')

  assert.deepEqual(calls, ['new'])
})
