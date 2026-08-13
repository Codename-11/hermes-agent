import { describe, expect, it } from 'vitest'

import { defaultBindings, KEYBIND_ACTIONS } from './actions'

describe('voice keybind actions', () => {
  it('ships dictation on a primary-modifier chord that works while the composer owns focus', () => {
    const byId = new Map(KEYBIND_ACTIONS.map(action => [action.id, action]))

    expect(byId.get('composer.dictate')).toMatchObject({ category: 'composer', defaults: ['mod+shift+d'] })
    expect(byId.get('composer.autoSpeak')).toMatchObject({ category: 'composer', defaults: [] })
    expect(byId.get('composer.wakeWord')).toMatchObject({ category: 'composer', defaults: [] })
    expect(byId.get('composer.voice')).toBeDefined()
  })

  it('keeps shipped defaults conflict-free', () => {
    const owners = new Map<string, string>()

    for (const [actionId, combos] of Object.entries(defaultBindings())) {
      for (const combo of combos) {
        expect(owners.get(combo), `${combo} is shared by ${owners.get(combo)} and ${actionId}`).toBeUndefined()
        owners.set(combo, actionId)
      }
    }
  })
})
