import { host } from '@hermes/plugin-sdk'
import { describe, expect, it, vi } from 'vitest'

import plugin from './plugin'

describe('Update Control plugin registration', () => {
  it('ships enabled and contributes a route-backed sidebar page without registering a startup pane', () => {
    const registerMany = vi.fn()
    const navigate = vi.spyOn(host, 'navigate').mockImplementation(() => undefined)

    plugin.register({ registerMany } as never)

    expect(plugin).toMatchObject({
      defaultEnabled: true,
      id: 'update-control',
      name: 'Update Control'
    })
    expect(registerMany).toHaveBeenCalledTimes(1)

    const contributions = registerMany.mock.calls[0]?.[0] as Array<{
      area: string
      data?: { label?: string; path?: string; run?: () => void }
      id: string
      render?: unknown
    }>

    expect(contributions.map(contribution => contribution.id)).toEqual(['page', 'nav', 'status', 'open'])
    expect(contributions.find(contribution => contribution.id === 'page')).toMatchObject({
      area: 'routes',
      data: { path: '/update-control' }
    })
    expect(contributions.some(contribution => contribution.area === 'panes')).toBe(false)
    expect(contributions.find(contribution => contribution.id === 'nav')).toMatchObject({
      area: 'sidebar.nav',
      data: { label: 'Update Control', path: '/update-control' }
    })
    expect(contributions.find(contribution => contribution.id === 'status')?.render).toBeTypeOf('function')
    expect(contributions.find(contribution => contribution.id === 'open')?.data?.label).toBe('Update Control: Open')

    contributions.find(contribution => contribution.id === 'open')?.data?.run?.()
    expect(navigate).toHaveBeenCalledWith('/update-control')
  })
})
