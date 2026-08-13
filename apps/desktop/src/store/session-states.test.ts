import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { findGroupOfPane, group, split } from '@/components/pane-shell/tree/model'
import { $layoutTree } from '@/components/pane-shell/tree/store'
import { $selectedStoredSessionId } from '@/store/session'
import type { SessionTile } from '@/store/session-states'
import {
  $sessionTiles,
  blankDraftTile,
  closeSessionTile,
  decodeSessionTileKey,
  decodeSessionTilePaneId,
  discardSessionTile,
  focusedSessionNeedsRoute,
  markSelectionRestore,
  nextSessionTileForWorkspace,
  openTileNeedsHydration,
  orderTilesByTree,
  patchSessionTile,
  selectionHomesToWorkspace,
  sessionTileKey,
  sessionTilePaneId
} from '@/store/session-states'

const tile = (storedSessionId: string, profile = 'default'): SessionTile => ({ profile, storedSessionId })
const tilePane = (id: string, profile = 'default') => sessionTilePaneId(profile, id)

describe('profile-qualified tile identity', () => {
  it('is reversible and collision-safe for profile and session punctuation', () => {
    const first = sessionTileKey('team:a/b%20', 'session:a/b%20')
    const second = sessionTileKey('team', 'a/b%20:session:a/b%20')

    expect(first).not.toBe(second)
    expect(decodeSessionTileKey(first)).toEqual({ profile: 'team:a/b%20', storedSessionId: 'session:a/b%20' })
    expect(decodeSessionTilePaneId(sessionTilePaneId('team:a/b%20', 'session:a/b%20'))).toEqual({
      legacy: false,
      profile: 'team:a/b%20',
      storedSessionId: 'session:a/b%20'
    })
  })

  it('keeps the same stored id distinct across profiles', () => {
    const tiles = [tile('same', 'default'), tile('same', 'worker')]
    const tree = group([tilePane('same', 'worker'), tilePane('same', 'default')])

    expect(orderTilesByTree(tree, tiles)).toEqual([tiles[1], tiles[0]])
  })

  it('recognizes legacy pane ids for migration', () => {
    expect(decodeSessionTilePaneId('session-tile:legacy-id')).toEqual({
      legacy: true,
      profile: 'default',
      storedSessionId: 'legacy-id'
    })
  })

  it('legacy bare-id mutations resolve one owner without touching its cloned-profile sibling', () => {
    $sessionTiles.set([tile('same', 'default'), tile('same', 'worker')])

    patchSessionTile('same', { error: 'default-only' })
    expect($sessionTiles.get()).toEqual([
      { error: 'default-only', profile: 'default', storedSessionId: 'same' },
      tile('same', 'worker')
    ])

    closeSessionTile('same')
    expect($sessionTiles.get()).toEqual([tile('same', 'worker')])

    discardSessionTile('same')
    expect($sessionTiles.get()).toEqual([])
  })

  it('explicit mutations target the requested profile when cloned ids coexist', () => {
    $sessionTiles.set([tile('same', 'default'), tile('same', 'worker')])

    patchSessionTile('same', { error: 'worker-only' }, 'worker')
    expect($sessionTiles.get()).toEqual([
      tile('same', 'default'),
      { error: 'worker-only', profile: 'worker', storedSessionId: 'same' }
    ])

    closeSessionTile('same', 'worker')
    expect($sessionTiles.get()).toEqual([tile('same', 'default')])
  })
})

describe('orderTilesByTree', () => {
  it('no-ops (null) without a tree or below two tiles', () => {
    expect(orderTilesByTree(null, [tile('a'), tile('b')])).toBeNull()
    expect(orderTilesByTree(group([tilePane('a')]), [tile('a')])).toBeNull()
  })

  it('reorders tiles to layout-tree encounter order across a split', () => {
    const tree = split('row', [group(['workspace', tilePane('b')]), group([tilePane('a')])])

    expect(orderTilesByTree(tree, [tile('a'), tile('b')])).toEqual([tile('b'), tile('a')])
  })

  it('returns null when the array already matches strip order (skip persist)', () => {
    const tree = split('row', [group([tilePane('b')]), group([tilePane('a')])])

    expect(orderTilesByTree(tree, [tile('b'), tile('a')])).toBeNull()
  })

  it('sorts not-yet-adopted tiles after placed ones, stably', () => {
    const tree = group(['workspace', tilePane('b')])

    expect(orderTilesByTree(tree, [tile('a'), tile('b'), tile('c')])).toEqual([tile('b'), tile('a'), tile('c')])
  })
})

describe('nextSessionTileForWorkspace', () => {
  afterEach(() => {
    $sessionTiles.set([])
    $layoutTree.set(null)
  })

  it('prefers a tab stacked directly with workspace', () => {
    $sessionTiles.set([tile('stacked'), tile('split')])
    $layoutTree.set(split('row', [group(['workspace', tilePane('stacked')]), group([tilePane('split')])]))

    expect(nextSessionTileForWorkspace()).toEqual(tile('stacked'))
  })

  it('falls back to a session tab in another split instead of trapping a blank workspace tab', () => {
    $sessionTiles.set([tile('split')])
    $layoutTree.set(split('row', [group(['workspace']), group([tilePane('split')])]))

    expect(nextSessionTileForWorkspace()).toEqual(tile('split'))
  })
})

describe('selectionHomesToWorkspace', () => {
  const tiles = [tile('a'), tile('b')]

  it('homes for a null selection or a non-tile session', () => {
    expect(selectionHomesToWorkspace(null, tiles)).toBe(true)
    expect(selectionHomesToWorkspace('c', tiles)).toBe(true)
  })

  it('skips homing when the selected id is already an open tile', () => {
    expect(selectionHomesToWorkspace('a', tiles)).toBe(false)
  })
})

describe('boot-restore selection homing (⌘R tab persistence)', () => {
  const mainGroup = () => group(['workspace', tilePane('t')], { active: tilePane('t'), id: 'main' })

  const activePane = () => {
    const tree = $layoutTree.get()

    return tree?.type === 'group' ? tree.active : null
  }

  it('a normal selection change fronts the workspace tab over an active tile', () => {
    $layoutTree.set(mainGroup())
    $selectedStoredSessionId.set('nav-1')

    expect(activePane()).toBe('workspace')
  })

  it('markSelectionRestore skips homing exactly once, so the persisted active tab survives a reload', () => {
    $layoutTree.set(mainGroup())
    markSelectionRestore()
    $selectedStoredSessionId.set('boot-1')

    // Boot restore: the tile tab the user reloaded on stays fronted.
    expect(activePane()).toBe(tilePane('t'))

    // One-shot consumed: the next selection change is a real navigation.
    $selectedStoredSessionId.set('nav-2')
    expect(activePane()).toBe('workspace')
  })
})

describe('focusedSessionNeedsRoute', () => {
  it('routes when the session is not on screen', () => {
    expect(focusedSessionNeedsRoute(null, false)).toBe(true)
    expect(focusedSessionNeedsRoute(null, true)).toBe(true)
  })

  it('routes for the ACTIVE main session while a full page covers the workspace', () => {
    expect(focusedSessionNeedsRoute('main', true)).toBe(true)
  })

  it('skips the route when the main session is already the visible chat', () => {
    expect(focusedSessionNeedsRoute('main', false)).toBe(false)
  })

  it('never routes for a tile — its pane shows the chat on any route', () => {
    expect(focusedSessionNeedsRoute('tile', true)).toBe(false)
    expect(focusedSessionNeedsRoute('tile', false)).toBe(false)
  })
})

describe('openTileNeedsHydration', () => {
  const state = (over: Partial<ClientSessionState> = {}) =>
    ({ busy: false, messages: [], storedSessionId: 'stored', ...over }) as ClientSessionState

  it('recovers an idle empty tile when its stored row says history exists', () => {
    expect(
      openTileNeedsHydration({ runtimeId: 'runtime', storedSessionId: 'stored' }, state(), {
        message_count: 6
      } as never)
    ).toBe(true)
  })

  it('recovers busy empty history but leaves healthy and genuinely empty tiles alone', () => {
    const bound = { runtimeId: 'runtime', storedSessionId: 'stored' }

    expect(
      openTileNeedsHydration(bound, state({ messages: [{ id: 'm1' }] as never }), { message_count: 6 } as never)
    ).toBe(false)
    expect(openTileNeedsHydration(bound, state({ busy: true }), { is_active: true, message_count: 0 } as never)).toBe(
      true
    )
    expect(openTileNeedsHydration(bound, state(), { message_count: 0 } as never)).toBe(false)
  })
})

describe('blankDraftTile', () => {
  const bound = (storedSessionId: string, runtimeId: string): SessionTile => ({
    profile: 'default',
    runtimeId,
    storedSessionId
  })

  const state = (messages: number, busy = false) =>
    ({ busy, messages: Array.from({ length: messages }, (_, i) => ({ id: `m${i}` })) }) as ClientSessionState

  it('finds the open tab whose session has no messages', () => {
    const tiles = [bound('a', 'run-a'), bound('b', 'run-b')]
    const states = { 'run-a': state(3), 'run-b': state(0) }

    expect(blankDraftTile(tiles, states)).toEqual(tiles[1])
  })

  it('picks the most recent blank tab when there are several', () => {
    const tiles = [bound('a', 'run-a'), bound('b', 'run-b')]
    const states = { 'run-a': state(0), 'run-b': state(0) }

    expect(blankDraftTile(tiles, states)).toEqual(tiles[1])
  })

  it('leaves a blank-but-busy tab alone — its first turn is already in flight', () => {
    expect(blankDraftTile([bound('a', 'run-a')], { 'run-a': state(0, true) })).toBeNull()
  })

  it('treats an unbound or unpublished tile as unknown, not empty', () => {
    expect(blankDraftTile([tile('a')], {})).toBeNull()
    expect(blankDraftTile([bound('a', 'run-a')], {})).toBeNull()
  })

  it('is null when every open tab holds a conversation', () => {
    expect(blankDraftTile([bound('a', 'run-a')], { 'run-a': state(2) })).toBeNull()
    expect(blankDraftTile([], {})).toBeNull()
  })
})

// ⌘⇧T used to only restore `$sessionTiles`. Adoption inserts silently
// (activate:false), so the tab came back behind the still-fronted workspace.
// Real path: register, adopt, focus — same as paneMirror + reopen.
describe('reopenLastClosedTile focuses the restored tab', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  afterEach(() => {
    vi.resetModules()
  })

  async function setup() {
    const tree = await import('@/components/pane-shell/tree/store')
    const model = await import('@/components/pane-shell/tree/model')
    const { registry } = await import('@/contrib/registry')
    const session = await import('@/store/session')
    const states = await import('@/store/session-states')

    registry.register({
      area: 'panes',
      data: { placement: 'main', uncloseable: true },
      id: 'workspace',
      render: () => null,
      title: 'chat'
    })

    // panes ← $sessionTiles (paneMirror stub). Adoption is synchronous on
    // register, so openSessionTile + focusOpenSession works the same tick.
    const registered = new Map<string, () => void>()

    const syncTiles = () => {
      const wanted = new Set(states.$sessionTiles.get().map(t => t.storedSessionId))

      for (const id of wanted) {
        if (registered.has(id)) {
          continue
        }

        registered.set(
          id,
          registry.register({
            area: 'panes',
            data: { dock: { pane: 'workspace', pos: 'center' }, placement: 'main' },
            id: tilePane(id),
            render: () => null,
            title: id
          })
        )
      }

      for (const [id, dispose] of registered) {
        if (!wanted.has(id)) {
          dispose()
          registered.delete(id)
          tree.removeTreePane(tilePane(id))
        }
      }
    }

    states.$sessionTiles.listen(syncTiles)
    tree.watchContributedPanes()
    session.$selectedStoredSessionId.set('primary')
    tree.declareDefaultTree(model.group(['workspace'], { active: 'workspace', id: 'grp-main' }))

    states.openSessionTile('closed', 'center', 'workspace')
    states.focusOpenSession('closed')
    tree.noteActiveTreeGroup('grp-main')
    expect(findGroupOfPane(tree.$layoutTree.get()!, tilePane('closed'))?.active).toBe(tilePane('closed'))

    return { states, tree }
  }

  it('fronts the restored tab after ⌘⇧T', async () => {
    const { states, tree } = await setup()

    states.closeSessionTile('closed')
    expect(states.$sessionTiles.get().some(t => t.storedSessionId === 'closed')).toBe(false)
    expect(findGroupOfPane(tree.$layoutTree.get()!, 'workspace')?.active).toBe('workspace')

    states.reopenLastClosedTile()

    expect(states.$sessionTiles.get().some(t => t.storedSessionId === 'closed')).toBe(true)
    expect(findGroupOfPane(tree.$layoutTree.get()!, tilePane('closed'))?.active).toBe(tilePane('closed'))
    expect(tree.$activeTreeGroup.get()).toBe('grp-main')
  })
})
