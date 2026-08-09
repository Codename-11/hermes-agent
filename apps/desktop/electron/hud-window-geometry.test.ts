import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  HUD_DEFAULT_HEIGHT,
  HUD_DEFAULT_WIDTH,
  hudBoundsForDrag,
  hudNativeWindowOptions,
  sanitizeHudState,
  shouldPinHudDragSize
} from './hud-window-geometry'

test('rejects persisted geometry produced by Windows drag growth', () => {
  const drifted = { x: 965, y: 59, width: 1132, height: 994 }

  assert.equal(sanitizeHudState(drifted, 'win32'), null)
  assert.deepEqual(sanitizeHudState(drifted, 'darwin'), drifted)
})

test('rejects fractional persisted geometry before passing it to BrowserWindow', () => {
  assert.equal(sanitizeHudState({ x: 100.5, y: 200, width: 620, height: 320 }, 'win32'), null)
  assert.equal(sanitizeHudState({ x: 100, y: 200, width: 620.5, height: 320 }, 'darwin'), null)
})

test('keeps deliberate HUD sizes within a compact two-times-default envelope', () => {
  assert.deepEqual(sanitizeHudState({ x: 100, y: 200, width: 900, height: 600 }, 'win32'), {
    x: 100,
    y: 200,
    width: 900,
    height: 600
  })
  assert.equal(
    sanitizeHudState({ x: 100, y: 200, width: HUD_DEFAULT_WIDTH * 2 + 1, height: 600 }, 'win32'),
    null
  )
  assert.equal(
    sanitizeHudState({ x: 100, y: 200, width: 900, height: HUD_DEFAULT_HEIGHT * 2 + 1 }, 'win32'),
    null
  )
})

test('disables native resize and pins drag size only on Windows', () => {
  assert.deepEqual(hudNativeWindowOptions('win32'), { resizable: false })
  assert.deepEqual(hudNativeWindowOptions('darwin'), { resizable: true })
  assert.deepEqual(hudNativeWindowOptions('linux'), { resizable: true })
  assert.equal(shouldPinHudDragSize('win32'), true)
  assert.equal(shouldPinHudDragSize('darwin'), false)
  assert.equal(shouldPinHudDragSize('linux'), false)
})

test('pins the snapshotted HUD size while dragging on Windows', () => {
  assert.deepEqual(hudBoundsForDrag([300, 200], { x: 12.4, y: -7.6 }, [620, 320]), {
    x: 312,
    y: 192,
    width: 620,
    height: 320
  })
})
