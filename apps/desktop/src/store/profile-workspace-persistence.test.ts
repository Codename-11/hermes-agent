import { beforeEach, describe, expect, it, vi } from 'vitest'

const loadWorkspaceStores = async () => {
  const profile = await import('./profile')
  const panes = await import('./panes')
  const routeTiles = await import('./route-tiles')
  const preview = await import('./preview')
  const layout = await import('./layout')
  const review = await import('./review')
  const composerPopout = await import('./composer-popout')
  const terminal = await import('@/app/right-sidebar/store')
  const tree = await import('@/components/pane-shell/tree/store')
  const model = await import('@/components/pane-shell/tree/model')

  return { composerPopout, layout, model, panes, preview, profile, review, routeTiles, terminal, tree }
}

const fileTarget = (path: string) => ({
  kind: 'file' as const,
  label: path.split('/').at(-1) ?? path,
  path,
  previewKind: 'text' as const,
  source: path,
  url: `file://${path}`
})

describe('window-scoped Desktop workspace persistence', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  it('keeps layout, pane geometry, page tabs, and previews stable while the gateway profile changes', async () => {
    const { composerPopout, layout, model, panes, preview, profile, review, routeTiles, terminal, tree } =
      await loadWorkspaceStores()

    const defaultTree = model.group(['workspace'], { id: 'grp-default' })

    const defaultCustom = model.split(
      'row',
      [model.group(['workspace', 'session-tile:default-chat'], { id: 'grp-default-main' }), model.group(['files'])],
      [3, 1],
      'split-default'
    )

    const workerCustom = model.split(
      'column',
      [model.group(['workspace', 'session-tile:worker-chat'], { id: 'grp-worker-main' }), model.group(['terminal'])],
      [2, 1],
      'split-worker'
    )

    tree.declareDefaultTree(defaultTree)
    tree.applyTree(defaultCustom, 'custom')
    panes.setPaneOpen('chat-sidebar', false)
    panes.setPaneWidthOverride('chat-sidebar', 301)
    routeTiles.openRouteTile('/skills', 'right')
    preview.openPreview(fileTarget('/default/readme.md'))
    layout.$panesFlipped.set(true)
    terminal.setTerminalTakeover(true)
    review.$reviewOpen.set(true)
    review.$reviewSelectedPath.set('/default/app.ts')
    composerPopout.setComposerPoppedOut('grp-default-main', true)

    profile.$activeGatewayProfile.set('worker')

    expect(tree.$layoutTree.get()).toEqual(defaultCustom)
    expect(tree.$activePresetId.get()).toBe('custom')
    expect(panes.getPaneStateSnapshot('chat-sidebar')).toEqual({ open: false, widthOverride: 301 })
    expect(routeTiles.$routeTiles.get()).toEqual([{ dir: 'right', path: '/skills' }])
    expect(preview.$previewTabs.get().map(tab => tab.target.path)).toEqual(['/default/readme.md'])
    expect(layout.$rightRailActiveTabId.get()).toContain('/default/readme.md')
    expect(layout.$panesFlipped.get()).toBe(true)
    expect(terminal.$terminalTakeover.get()).toBe(true)
    expect(review.$reviewOpen.get()).toBe(true)
    expect(review.$reviewSelectedPath.get()).toBe('/default/app.ts')
    expect(composerPopout.$composerPopoutZones.get()['grp-default-main']?.poppedOut).toBe(true)

    tree.applyTree(workerCustom, 'terminal-deck')
    panes.setPaneOpen('chat-sidebar', true)
    panes.setPaneWidthOverride('chat-sidebar', 260)
    routeTiles.openRouteTile('/artifacts', 'left')
    preview.openPreview(fileTarget('/worker/notes.md'))
    composerPopout.setComposerPopoutPosition('grp-worker-main', { bottom: 32, right: 48 }, { persist: true })

    // Applying a tree adopts every currently registered pane into it before a
    // profile change happens. Snapshot that settled window state; the behavior
    // under test is that gateway routing does not replace it.
    const settledTree = tree.$layoutTree.get()

    profile.$activeGatewayProfile.set('default')

    expect(tree.$layoutTree.get()).toEqual(settledTree)
    expect(tree.$activePresetId.get()).toBe('terminal-deck')
    expect(panes.getPaneStateSnapshot('chat-sidebar')).toEqual({ open: true, widthOverride: 260 })
    expect(routeTiles.$routeTiles.get()).toEqual([
      { dir: 'right', path: '/skills' },
      { dir: 'left', path: '/artifacts' }
    ])
    expect(preview.$previewTabs.get().map(tab => tab.target.path)).toEqual(['/default/readme.md', '/worker/notes.md'])
    expect(layout.$rightRailActiveTabId.get()).toContain('/worker/notes.md')
    expect(layout.$panesFlipped.get()).toBe(true)
    expect(terminal.$terminalTakeover.get()).toBe(true)
    expect(review.$reviewOpen.get()).toBe(true)
    expect(review.$reviewSelectedPath.get()).toBe('/default/app.ts')
    expect(composerPopout.$composerPopoutZones.get()['grp-default-main']?.poppedOut).toBe(true)
  })
})
