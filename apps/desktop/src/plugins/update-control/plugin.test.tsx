import { describe, expect, it, vi } from 'vitest'

import plugin from './plugin'

describe('Update Control plugin registration', () => {
  it('ships opt-in and contributes the page, navigation, status, and palette action', () => {
    const registerMany = vi.fn()

    plugin.register({ registerMany } as never)

    expect(plugin.defaultEnabled).toBe(false)
    expect(plugin.id).toBe('update-control')

    const contributions = registerMany.mock.calls[0]?.[0] as Array<{
      area: string
      data?: { label?: string; path?: string }
      id: string
      render?: unknown
    }>

    expect(contributions.map(contribution => contribution.id)).toEqual(['page', 'nav', 'status', 'open'])
    expect(contributions.find(contribution => contribution.id === 'page')).toMatchObject({
      area: 'routes',
      data: { path: '/update-control' }
    })
    expect(contributions.find(contribution => contribution.id === 'nav')).toMatchObject({
      area: 'sidebar.nav',
      data: { label: 'Update Control', path: '/update-control' }
    })
    expect(contributions.find(contribution => contribution.id === 'status')?.render).toBeTypeOf('function')
    expect(contributions.find(contribution => contribution.id === 'open')?.data?.label).toBe('Update Control: Open')
  })
})
