import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $notifications, clearNotifications } from '@/store/notifications'
import { $wakeWord, applyWakeStartResult, applyWakeStatus, resetWakeWordState } from '@/store/wake-word'

import { toggleWakeWordFromKeyboard } from './use-keybinds'

beforeEach(() => {
  clearNotifications()
  resetWakeWordState()
})

describe('wake-word keybind handler', () => {
  it('reports a backend refusal to the keyboard user', async () => {
    applyWakeStatus({ available: true, listening: false, phrase: 'hey hermes' })

    await toggleWakeWordFromKeyboard('Toggle wake word', async () => {
      applyWakeStartResult({ reason: 'owned', started: false })
    })

    expect($notifications.get()[0]).toMatchObject({ id: 'wake-word-keybind-failed', kind: 'error' })
  })

  it('does not call the backend while a wake toggle is pending', async () => {
    $wakeWord.set({ ...$wakeWord.get(), notice: 'arming', pending: true })
    const toggle = vi.fn(async () => undefined)

    await toggleWakeWordFromKeyboard('Toggle wake word', toggle)

    expect(toggle).not.toHaveBeenCalled()
    expect($notifications.get()[0]).toMatchObject({ id: 'wake-word-keybind-pending', kind: 'warning' })
  })
})
