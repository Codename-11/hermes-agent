import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type * as Nanostores from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ProfileInfo } from '@/types/hermes'

import { ProfileVisibilitySettings } from './profile-visibility-settings'

const stores = vi.hoisted(() => {
  const { atom } = require('nanostores') as typeof Nanostores

  return {
    hidden: atom<string[]>(['ops']),
    order: atom<string[]>([]),
    profiles: atom<ProfileInfo[]>([
      {
        has_env: false,
        is_default: true,
        model: null,
        name: 'default',
        path: '/tmp/default',
        provider: null,
        skill_count: 0
      },
      {
        has_env: false,
        is_default: false,
        model: null,
        name: 'ops',
        path: '/tmp/ops',
        provider: null,
        skill_count: 0
      }
    ])
  }
})

const setProfileHidden = vi.hoisted(() => vi.fn())

vi.mock('@/store/profile', () => ({
  $hiddenProfiles: stores.hidden,
  $profileOrder: stores.order,
  $profiles: stores.profiles,
  normalizeProfileKey: (name: string) => name || 'default',
  setProfileHidden,
  sortByProfileOrder: (profiles: ProfileInfo[]) => profiles
}))

afterEach(() => {
  cleanup()
  setProfileHidden.mockClear()
})

describe('ProfileVisibilitySettings', () => {
  it('shows hidden profiles as off and can restore them', () => {
    render(<ProfileVisibilitySettings />)

    const toggle = screen.getByRole('switch', { name: 'Show ops' })

    expect(toggle.getAttribute('data-state')).toBe('unchecked')
    fireEvent.click(toggle)
    expect(setProfileHidden).toHaveBeenCalledWith('ops', false)
  })
})