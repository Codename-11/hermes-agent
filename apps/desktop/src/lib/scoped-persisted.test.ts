import { beforeEach, describe, expect, it } from 'vitest'

import { Codecs } from './persisted'
import { createScopedPersistence } from './scoped-persisted'

beforeEach(() => window.localStorage.clear())

describe('createScopedPersistence', () => {
  it('reloads and writes each scope without echoing loaded values', () => {
    const persistence = createScopedPersistence<string>({
      initialScope: 'local',
      storageKey: (key, scope) => (scope === 'local' ? key : `${key}.${scope}`)
    })
    const $value = persistence.scopedPersistentAtom('setting', 'fresh', Codecs.text)

    $value.set('local-value')
    persistence.setScope('remote')
    expect($value.get()).toBe('fresh')

    $value.set('remote-value')
    persistence.setScope('local')
    expect($value.get()).toBe('local-value')
    expect(window.localStorage.getItem('setting.remote')).toBe('remote-value')
  })

  it('supports explicit persistence for hot in-memory updates', () => {
    const persistence = createScopedPersistence<string>({
      initialScope: 'workspace',
      storageKey: (key, scope) => `${key}.${scope}`
    })
    const $value = persistence.scopedPersistentAtom('layout', 'fresh', Codecs.text, { autoPersist: false })

    $value.set('drag-frame')
    expect(window.localStorage.getItem('layout.workspace')).toBeNull()

    $value.persistCurrent()
    expect(window.localStorage.getItem('layout.workspace')).toBe('drag-frame')
  })
})
