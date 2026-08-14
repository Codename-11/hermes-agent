import { Codecs } from '@/lib/persisted'
import { profilePersistentAtom } from '@/lib/profile-persisted'

import { findGroupOfPane, findParentSplit, type LayoutNode } from './model'

const DISMISSED_KEY = 'hermes.desktop.dismissedPanes.v1'
const PROFILE_DISMISSED_KEY = 'hermes.desktop.profileDismissedPanes.v1'
const PANE_SHARE_KEY = 'hermes.desktop.paneShare.v1'
const PROFILE_PANE_SHARE_KEY = 'hermes.desktop.profilePaneShares.v1'

export const $dismissedPanes = profilePersistentAtom<ReadonlySet<string>>({
  codec: {
    decode: raw => new Set(Codecs.stringArray.decode(raw)),
    encode: value => Codecs.stringArray.encode([...value])
  },
  fallback: () => new Set(),
  key: PROFILE_DISMISSED_KEY,
  legacyKey: DISMISSED_KEY
})

export function setPaneDismissed(paneId: string, dismissed: boolean): void {
  const current = $dismissedPanes.get()
  if (current.has(paneId) === dismissed) {
    return
  }

  const next = new Set(current)
  if (dismissed) {
    next.add(paneId)
  } else {
    next.delete(paneId)
  }
  $dismissedPanes.set(next)
}

function validShare(share: unknown): share is number {
  return typeof share === 'number' && Number.isFinite(share) && share > 0 && share < 1
}

export function sanitizePaneShares(value: unknown): Record<string, number> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value).filter(([, share]) => validShare(share)))
    : {}
}

const $paneShares = profilePersistentAtom<Record<string, number>>({
  codec: Codecs.json(sanitizePaneShares),
  fallback: () => ({}),
  key: PROFILE_PANE_SHARE_KEY,
  legacyKey: PANE_SHARE_KEY
})

export function rememberPaneShare(tree: LayoutNode, paneId: string): void {
  const zone = findGroupOfPane(tree, paneId)
  if (!zone || zone.panes.length !== 1) {
    return
  }

  const parent = findParentSplit(tree, zone.id)
  if (!parent) {
    return
  }

  const at = parent.children.findIndex(child => child.id === zone.id)
  const partner = at > 0 ? at - 1 : at + 1
  const pair = (parent.weights[at] ?? 1) + (parent.weights[partner] ?? 1)
  const share = pair > 0 ? (parent.weights[at] ?? 1) / pair : null

  if (validShare(share)) {
    $paneShares.set({ ...$paneShares.get(), [paneId]: share })
  }
}

export function edgeWeightsForShare(share: unknown): [number, number] | undefined {
  return validShare(share) ? [1 - share, share] : undefined
}

export function recalledPaneEdgeWeights(paneId: string): [number, number] | undefined {
  return edgeWeightsForShare($paneShares.get()[paneId])
}
