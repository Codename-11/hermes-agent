import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('Hermes Bots persisted pane migration', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  afterEach(() => {
    vi.resetModules()
  })

  it('adopts the fresh pane below Sessions without resetting unrelated layout', async () => {
    const model = await import('./model')

    const oldPane = 'hermes-bots:pane'
    const newPane = 'hermes-bots:bots-dock'

    const sessions = model.group(['sessions', oldPane], {
      active: 'sessions',
      headerHidden: false,
      id: 'grp-sessions'
    })

    const terminal = model.group(['terminal'], { active: 'terminal', id: 'grp-terminal', minimized: true })
    const main = model.group(['workspace', 'notes'], { active: 'notes', id: 'grp-main' })
    const left = model.split('column', [sessions, terminal], [5, 2], 'split-left')
    const persisted = model.split('row', [left, main], [1, 3], 'split-root')

    window.localStorage.setItem(
      'hermes.desktop.profileLayoutTrees.v1',
      JSON.stringify({ default: JSON.stringify(persisted) })
    )

    const tree = await import('./store')
    const { registry } = await import('@/contrib/registry')

    expect(model.allPaneIds(tree.$layoutTree.get()!)).toContain(oldPane)

    registry.registerMany([
      { area: 'panes', data: { placement: 'left' }, id: 'sessions', render: () => null },
      { area: 'panes', data: { placement: 'bottom' }, id: 'terminal', render: () => null },
      { area: 'panes', data: { placement: 'main' }, id: 'workspace', render: () => null },
      { area: 'panes', data: { placement: 'main' }, id: 'notes', render: () => null },
      {
        area: 'panes',
        data: { placement: 'left', width: '260px', dock: { pane: 'sessions', pos: 'bottom' } },
        id: newPane,
        render: () => null,
        source: 'plugin:hermes-bots',
        title: 'Bots'
      }
    ])
    tree.watchContributedPanes()

    const migrated = tree.$layoutTree.get()!
    const sessionsAfter = model.findGroupOfPane(migrated, 'sessions')!
    const botsAfter = model.findGroupOfPane(migrated, newPane)!
    const terminalAfter = model.findGroup(migrated, 'grp-terminal')!
    const mainAfter = model.findGroup(migrated, 'grp-main')!
    const botsParent = model.findParentSplit(migrated, botsAfter.id)!
    const livePaneIds = registry.getArea('panes').map(pane => pane.id)

    expect(migrated).toMatchObject({ id: 'split-root', orientation: 'row', weights: [1, 3] })
    expect(sessionsAfter).toEqual(sessions)
    expect(terminalAfter).toEqual(terminal)
    expect(mainAfter).toEqual(main)
    expect(botsParent).toMatchObject({ id: 'split-left', orientation: 'column' })
    expect(botsParent.children.indexOf(botsAfter)).toBe(botsParent.children.indexOf(sessionsAfter) + 1)
    expect(livePaneIds).toContain(newPane)
    expect(livePaneIds).not.toContain(oldPane)
  })
})