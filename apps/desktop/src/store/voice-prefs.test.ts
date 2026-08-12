import { describe, expect, it } from 'vitest'

import {
  $autoSpeakReplyConversations,
  $voiceStopPhrase,
  applyVoiceStopPhraseFromConfig,
  autoSpeakRepliesEnabled,
  setAutoSpeakReplies
} from './voice-prefs'

describe('setAutoSpeakReplies', () => {
  it('persists independently by profile and durable conversation id', async () => {
    $autoSpeakReplyConversations.set({})

    await setAutoSpeakReplies(true, 'conversation-1', 'victor')

    expect(autoSpeakRepliesEnabled('victor', 'conversation-1')).toBe(true)
    expect(autoSpeakRepliesEnabled('mizu', 'conversation-1')).toBe(false)
    expect(autoSpeakRepliesEnabled('victor', 'conversation-2')).toBe(false)
  })

  it('removes only the selected conversation preference', async () => {
    $autoSpeakReplyConversations.set({})
    await setAutoSpeakReplies(true, 'conversation-1', 'victor')
    await setAutoSpeakReplies(true, 'conversation-2', 'victor')

    await setAutoSpeakReplies(false, 'conversation-1', 'victor')

    expect(autoSpeakRepliesEnabled('victor', 'conversation-1')).toBe(false)
    expect(autoSpeakRepliesEnabled('victor', 'conversation-2')).toBe(true)
  })

  it('does not create a global fallback for unsent drafts', async () => {
    $autoSpeakReplyConversations.set({})

    await setAutoSpeakReplies(true, null, 'victor')

    expect($autoSpeakReplyConversations.get()).toEqual({})
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
