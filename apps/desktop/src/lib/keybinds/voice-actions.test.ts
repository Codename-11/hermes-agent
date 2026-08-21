import { describe, expect, it } from 'vitest'

import { KEYBIND_ACTIONS } from './actions'

describe('voice keybind actions', () => {
  it('registers focused actions for dictation, auto-speak, and wake word', () => {
    const byId = new Map(KEYBIND_ACTIONS.map(action => [action.id, action]))

    expect(byId.get('composer.dictate')).toMatchObject({ category: 'composer', defaults: ['mod+shift+d'] })
    expect(byId.get('composer.autoSpeak')).toMatchObject({ category: 'composer', defaults: [] })
    expect(byId.get('composer.wakeWord')).toMatchObject({ category: 'composer', defaults: [] })
  })
})
