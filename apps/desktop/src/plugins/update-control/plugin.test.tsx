import { describe, expect, it, vi } from 'vitest'

import plugin from './plugin'

describe('Update Control plugin registration', () => {
  it('ships opt-in and contributes a closable main tab, status, and palette opener', () => {
    const registerMany = vi.fn()
    const reveal = vi.fn()

    plugin.register({ panes: { reveal }, registerMany } as never)

    expect(plugin).toMatchObject({
      defaultEnabled: false,
      id: 'update-control',
      name: 'Update Control'
    })
    expect(registerMany).toHaveBeenCalledTimes(1)

    const contributions = registerMany.mock.calls[0]?.[0] as Array<{
      area: string
      data?: { closeBehavior?: string; label?: string; placement?: string; run?: () => void }
      id: string
      render?: unknown
    }>

    expect(contributions.map(contribution => contribution.id)).toEqual(['panel', 'nav', 'status', 'open'])
    expect(contributions.find(contribution => contribution.id === 'panel')).toMatchObject({
      area: 'panes',
      title: 'Update Control',
      data: { closeBehavior: 'dismiss', placement: 'main' }
    })
    expect(contributions.find(contribution => contribution.id === 'nav')).toMatchObject({
      area: 'sidebar.nav',
      data: { label: 'Update Control' }
    })
    expect(contributions.find(contribution => contribution.id === 'status')?.render).toBeTypeOf('function')
    expect(contributions.find(contribution => contribution.id === 'open')?.data?.label).toBe('Update Control: Open')

    contributions.find(contribution => contribution.id === 'open')?.data?.run?.()
    expect(reveal).toHaveBeenCalledWith('panel')
  })
})
