import { describe, expect, it, vi } from 'vitest'

import type { SidebarNavItem } from '../../types'

import { activateSidebarNavItem, resolveSidebarNavContribution } from './sidebar-nav'

const item = (overrides: Partial<SidebarNavItem> = {}): SidebarNavItem => ({
  icon: () => null,
  id: 'plugin:control',
  label: 'Control',
  ...overrides
})

describe('resolveSidebarNavContribution', () => {
  it('accepts a direct action without requiring a route', () => {
    const onSelect = vi.fn()

    expect(resolveSidebarNavContribution({ codicon: 'settings-gear', label: 'Control', onSelect })).toEqual({
      codicon: 'settings-gear',
      label: 'Control',
      onSelect,
      route: undefined
    })
  })

  it('accepts an absolute route and rejects inert or relative rows', () => {
    expect(resolveSidebarNavContribution({ codicon: '', label: 'Page', path: '/page' })).toEqual({
      codicon: 'plug',
      label: 'Page',
      onSelect: undefined,
      route: '/page'
    })
    expect(resolveSidebarNavContribution({ label: 'Inert' })).toBeNull()
    expect(resolveSidebarNavContribution({ label: 'Relative', path: 'page' })).toBeNull()
    expect(resolveSidebarNavContribution({ label: 'Wrong action type', onSelect: 'open' })).toBeNull()
    expect(resolveSidebarNavContribution('not an object')).toBeNull()
  })
})

describe('activateSidebarNavItem', () => {
  it('runs a direct action instead of navigating', () => {
    const onSelect = vi.fn()
    const navigate = vi.fn()
    const navItem = item({ onSelect, route: '/ignored' })

    activateSidebarNavItem(navItem, navigate)

    expect(onSelect).toHaveBeenCalledOnce()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('delegates route rows to the existing navigation handler', () => {
    const navigate = vi.fn()
    const navItem = item({ route: '/page' })

    activateSidebarNavItem(navItem, navigate)

    expect(navigate).toHaveBeenCalledWith(navItem)
  })
})
