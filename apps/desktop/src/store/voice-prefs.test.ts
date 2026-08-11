import { describe, expect, it, vi } from 'vitest'

import { getHermesConfigRecord, saveHermesConfig } from '@/hermes'

vi.mock('@/hermes', () => ({
  getHermesConfigRecord: vi.fn(async () => ({})),
  saveHermesConfig: vi.fn(async () => undefined)
}))

import { $autoSpeakReplies, $voiceStopPhrase, applyVoiceStopPhraseFromConfig, setAutoSpeakReplies } from './voice-prefs'

describe('setAutoSpeakReplies', () => {
  it('optimistically persists through voice.auto_tts while preserving other voice config', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValueOnce({ voice: { provider: 'openai' } })
    $autoSpeakReplies.set(false)

    await setAutoSpeakReplies(true)

    expect($autoSpeakReplies.get()).toBe(true)
    expect(saveHermesConfig).toHaveBeenCalledWith({ voice: { auto_tts: true, provider: 'openai' } })
  })

  it('reverts the optimistic value when persistence fails', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValueOnce({})
    vi.mocked(saveHermesConfig).mockRejectedValueOnce(new Error('read-only config'))
    $autoSpeakReplies.set(false)

    await expect(setAutoSpeakReplies(true)).rejects.toThrow('read-only config')
    expect($autoSpeakReplies.get()).toBe(false)
  })
})

describe('applyVoiceStopPhraseFromConfig', () => {
  it('defaults to "stop" when the key is absent (backend default applies)', () => {
    applyVoiceStopPhraseFromConfig({ voice: {} })
    expect($voiceStopPhrase.get()).toBe('stop')

    applyVoiceStopPhraseFromConfig(null)
    expect($voiceStopPhrase.get()).toBe('stop')
  })

  it('uses the first configured phrase so a custom phrase renders correctly', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: ['goodbye hermes', 'stop'] } })
    expect($voiceStopPhrase.get()).toBe('goodbye hermes')
  })

  it('coerces a bare string like the backend does', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: 'halt' } })
    expect($voiceStopPhrase.get()).toBe('halt')
  })

  it('null phrase when stop phrases are disabled — no notice is shown', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: [] } })
    expect($voiceStopPhrase.get()).toBeNull()
  })

  it('malformed entries are skipped; all-blank list disables', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: ['  ', ''] } })
    expect($voiceStopPhrase.get()).toBeNull()
  })
})
