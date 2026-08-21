import { EventEmitter } from 'node:events'

import { describe, expect, it } from 'vitest'

import { bindHudCloseBehavior, suppressHudCloseBehavior } from './hud-window-lifecycle'

describe('HUD close behavior', () => {
  it('suppresses only restore behavior while preserving resource cleanup listeners', () => {
    const win = new EventEmitter()
    const calls: string[] = []

    win.once('closed', () => calls.push('cleanup'))
    bindHudCloseBehavior(win, () => calls.push('restore'))
    suppressHudCloseBehavior(win)
    win.emit('closed')

    expect(calls).toEqual(['cleanup'])
  })

  it('replaces only the prior behavior handler when rebound', () => {
    const win = new EventEmitter()
    const calls: string[] = []

    bindHudCloseBehavior(win, () => calls.push('old'))
    bindHudCloseBehavior(win, () => calls.push('new'))
    win.emit('closed')

    expect(calls).toEqual(['new'])
  })
})
