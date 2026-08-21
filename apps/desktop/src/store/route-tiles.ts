import { workspaceScopedAtom } from '@/lib/workspace-scope'

import type { SplitDir } from './session-states'

/**
 * Route (page) tiles — a full-page view (Capabilities / Messaging / Artifacts,
 * or any plugin route) rendered as a layout-tree pane BESIDE the main thread,
 * the page analog of session tiles. Persisted by path so they re-open on boot.
 */
export interface RouteTile {
  /** The route path this tile renders, e.g. `/skills`. */
  path: string
  /** Edge to dock against main on adoption (default right). */
  dir?: SplitDir
}

const TILES_KEY = 'hermes.desktop.routeTiles.v1'

export const $routeTiles = workspaceScopedAtom<RouteTile[]>(TILES_KEY, [], {
  decode: raw => {
    const parsed = JSON.parse(raw) as unknown

    return Array.isArray(parsed)
      ? parsed
          .filter((t): t is RouteTile => Boolean(t && typeof (t as RouteTile).path === 'string'))
          .map(t => ({ dir: t.dir, path: t.path }))
      : []
  },
  encode: tiles => (tiles.length === 0 ? null : JSON.stringify(tiles))
})

function saveTiles(tiles: RouteTile[]) {
  $routeTiles.set(tiles)
}

/** Open (or front) a page tile for a route, docked on `dir` (default right).
 *  Idempotent — an already-open tile keeps its original edge. */
export function openRouteTile(path: string, dir: SplitDir = 'right') {
  const tiles = $routeTiles.get()

  if (!tiles.some(t => t.path === path)) {
    saveTiles([...tiles, { dir, path }])
  }
}

export function closeRouteTile(path: string) {
  saveTiles($routeTiles.get().filter(t => t.path !== path))
}
