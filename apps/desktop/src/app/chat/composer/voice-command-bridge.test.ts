import { afterEach, describe, expect, it } from 'vitest'

import {
  markActiveComposer,
  onComposerAutoSpeakToggleRequest,
  onComposerDictationToggleRequest,
  requestAutoSpeakToggle,
  requestDictationToggle
} from './focus'
import { canToggleDictation } from './hooks/use-composer-voice'

afterEach(() => markActiveComposer('main'))

describe('focused voice command bridge', () => {
  it('rejects dictation while unavailable, disabled, or already transcribing', () => {
    expect(canToggleDictation({ available: false, disabled: false, status: 'idle' })).toBe(false)
    expect(canToggleDictation({ available: true, disabled: true, status: 'idle' })).toBe(false)
    expect(canToggleDictation({ available: true, disabled: false, status: 'transcribing' })).toBe(false)
    expect(canToggleDictation({ available: true, disabled: false, status: 'recording' })).toBe(true)
  })

  it('routes dictation and auto-speak to the active composer only', async () => {
    const seen: string[] = []
    markActiveComposer('tile:voice')

    const offDictate = onComposerDictationToggleRequest(target => seen.push(`dictate:${target}`))
    const offAutoSpeak = onComposerAutoSpeakToggleRequest(target => seen.push(`auto:${target}`))

    requestDictationToggle()
    requestAutoSpeakToggle()
    await new Promise(resolve => window.setTimeout(resolve, 0))

    offDictate()
    offAutoSpeak()
    expect(seen).toEqual(['dictate:tile:voice', 'auto:tile:voice'])
  })
})
