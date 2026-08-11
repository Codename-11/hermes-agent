import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $notifications, clearNotifications } from '@/store/notifications'
import { $autoSpeakReplies } from '@/store/voice-prefs'
import { $wakeWord, applyWakeStartResult, applyWakeStatus, resetWakeWordState } from '@/store/wake-word'

import { toggleAutoSpeakFromKeyboard, toggleWakeWordFromKeyboard } from './use-keybinds'

beforeEach(() => {
  clearNotifications()
  $autoSpeakReplies.set(false)
  resetWakeWordState()
})

describe('voice keybind handlers', () => {
  it('uses the profile-backed auto-speak persistence seam', async () => {
    const persist = vi.fn(async (enabled: boolean) => $autoSpeakReplies.set(enabled))

    await toggleAutoSpeakFromKeyboard('Autosave failed', persist)

    expect(persist).toHaveBeenCalledWith(true)
    expect($autoSpeakReplies.get()).toBe(true)
  })

  it('surfaces auto-speak persistence failure', async () => {
    await toggleAutoSpeakFromKeyboard('Autosave failed', async () => {
      throw new Error('disk read-only')
    })

    expect($notifications.get()[0]).toMatchObject({ kind: 'error', title: 'Autosave failed', message: 'disk read-only' })
  })

  it('accepts a successful wake toggle without inventing a notification', async () => {
    applyWakeStatus({ available: true, listening: false, phrase: 'hey hermes' })

    await toggleWakeWordFromKeyboard('Toggle Hey Hermes wake word', async () => {
      applyWakeStartResult({ started: true })
    })

    expect($wakeWord.get().listening).toBe(true)
    expect($notifications.get()).toEqual([])
  })

  it('shows keyboard-visible feedback for backend refusal or failure', async () => {
    applyWakeStatus({ available: true, listening: false, phrase: 'hey hermes' })

    await toggleWakeWordFromKeyboard('Toggle Hey Hermes wake word', async () => {
      applyWakeStartResult({ reason: 'owned', started: false })
    })

    expect($wakeWord.get().listening).toBe(false)
    expect($notifications.get()[0]).toMatchObject({
      id: 'wake-word-keybind-failed',
      kind: 'error',
      detail: 'another surface owns the listener'
    })
  })

  it('does not call the backend while a wake toggle is pending and explains why', async () => {
    $wakeWord.set({ ...$wakeWord.get(), notice: 'arming', pending: true })
    const toggle = vi.fn(async () => undefined)

    await toggleWakeWordFromKeyboard('Toggle Hey Hermes wake word', toggle)

    expect(toggle).not.toHaveBeenCalled()
    expect($notifications.get()[0]).toMatchObject({ id: 'wake-word-keybind-pending', kind: 'warning', detail: 'arming' })
  })
})
