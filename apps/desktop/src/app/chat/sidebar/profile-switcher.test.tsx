import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type * as Nanostores from 'nanostores'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ProfileInfo } from '@/types/hermes'

import { ProfileRail } from './profile-switcher'

const stores = vi.hoisted(() => {
  const { atom } = require('nanostores') as typeof Nanostores

  return {
  active: atom('default'),
  colors: atom<Record<string, string>>({}),
  createRequest: atom(0),
  hidden: atom<string[]>(['hidden-agent']),
  order: atom<string[]>([]),
  profiles: atom<ProfileInfo[]>([
    {
      has_env: false,
      is_default: true,
      model: null,
      name: 'default',
      path: '/tmp/hermes/default',
      provider: null,
      skill_count: 0
    },
    {
      has_env: false,
      is_default: false,
      model: null,
      name: 'visible-agent',
      path: '/tmp/hermes/visible-agent',
      provider: null,
      skill_count: 0
    },
    {
      has_env: false,
      is_default: false,
      model: null,
      name: 'hidden-agent',
      path: '/tmp/hermes/hidden-agent',
      provider: null,
      skill_count: 0
    }
  ]),
  scope: atom('default')
  }
})

const newSessionInProfile = vi.hoisted(() => vi.fn())
const newSessionTabAction = vi.hoisted(() => vi.fn())
const openNewWindow = vi.hoisted(() => vi.fn())

const newSessionTabActionStore = vi.hoisted(() => {
  const { atom } = require('nanostores') as typeof Nanostores

  return atom<((profile?: string) => void) | null>(newSessionTabAction)
})

vi.mock('@/store/profile', () => ({
  $activeGatewayProfile: stores.active,
  $hiddenProfiles: stores.hidden,
  $profileColors: stores.colors,
  $profileCreateRequest: stores.createRequest,
  $profileOrder: stores.order,
  $profiles: stores.profiles,
  $profileScope: stores.scope,
  ALL_PROFILES: '__all__',
  filterVisibleProfiles: (profiles: ProfileInfo[], hidden: string[]) =>
    profiles.filter(item => item.is_default || !hidden.includes(item.name)),
  newSessionInProfile,
  normalizeProfileKey: (name: null | string | undefined) => (name ?? '').trim() || 'default',
  profileLabel: (profile: ProfileInfo) => profile.display_name?.trim() || profile.name,
  refreshActiveProfile: vi.fn(async () => undefined),
  selectProfile: vi.fn(),
  setProfileColor: vi.fn(),
  setProfileOrder: vi.fn(),
  setShowAllProfiles: vi.fn(),
  sortByProfileOrder: (profiles: ProfileInfo[]) => profiles
}))

vi.mock('@/components/pane-shell/tree/store', () => ({ $newSessionTabAction: newSessionTabActionStore }))
vi.mock('@/store/windows', () => ({ openNewWindow }))

vi.mock('@/hermes', () => ({ getProfileSoul: vi.fn(), updateProfileSoul: vi.fn() }))
vi.mock('@/store/profile-share', () => ({ runExportProfileFlow: vi.fn(), runImportProfileFlow: vi.fn() }))
vi.mock('./use-profile-prewarm', () => ({ useProfilePrewarm: () => ({ cancelPrewarm: vi.fn(), startPrewarm: vi.fn() }) }))
vi.mock('../../profiles/create-profile-dialog', () => ({ CreateProfileDialog: () => null }))
vi.mock('../../profiles/delete-profile-dialog', () => ({ DeleteProfileDialog: () => null }))
vi.mock('../../profiles/rename-profile-dialog', () => ({ RenameProfileDialog: () => null }))
vi.mock('@/components/chat/code-editor', () => ({ CodeEditor: () => null }))

afterEach(() => {
  cleanup()
  newSessionInProfile.mockClear()
  newSessionTabAction.mockClear()
  openNewWindow.mockClear()
})

describe('ProfileRail profile visibility and actions', () => {
  it('omits hidden profile icons', () => {
    render(
      <MemoryRouter>
        <ProfileRail />
      </MemoryRouter>
    )

    expect(screen.getByRole('button', { name: 'visible-agent' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'hidden-agent' })).toBeNull()
  })

  it('opens a new chat tab in the current window for the right-clicked profile', async () => {
    render(
      <MemoryRouter>
        <ProfileRail />
      </MemoryRouter>
    )

    const icon = screen.getByRole('button', { name: 'visible-agent' })
    fireEvent.pointerDown(icon, { button: 2, pointerType: 'mouse' })
    fireEvent.contextMenu(icon, { button: 2 })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New chat' }))

    expect(newSessionTabAction).toHaveBeenCalledWith('visible-agent')
    expect(newSessionInProfile).not.toHaveBeenCalled()
  })

  it('offers a separate new window action for the right-clicked profile', async () => {
    render(
      <MemoryRouter>
        <ProfileRail />
      </MemoryRouter>
    )

    const icon = screen.getByRole('button', { name: 'visible-agent' })
    fireEvent.pointerDown(icon, { button: 2, pointerType: 'mouse' })
    fireEvent.contextMenu(icon, { button: 2 })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New window' }))

    expect(openNewWindow).toHaveBeenCalledWith('visible-agent')
  })
})