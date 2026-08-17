import type { MouseEvent } from 'react'

import { isEditableTarget } from '@/lib/keybinds/combo'

/**
 * Keep a broad context-menu trigger as a fallback rather than an override.
 * Place this handler on a child of the Radix trigger: stopping propagation on
 * the trigger itself is too late because Radix merges its handler there.
 * Never prevent the default; Electron still needs to open native edit/media
 * menus when this guard declines the fallback.
 */
export function guardFallbackContextMenu(event: MouseEvent<HTMLElement>, ownerAttribute: string) {
  const target = event.target as HTMLElement | null
  const owner = target?.closest('[data-slot="context-menu-trigger"]')
  const selection = window.getSelection()
  const hasTextSelection = Boolean(selection && !selection.isCollapsed && selection.toString().length > 0)

  if (
    (owner && !owner.hasAttribute(ownerAttribute)) ||
    target?.closest('img, picture, video, canvas') ||
    isEditableTarget(target) ||
    hasTextSelection
  ) {
    event.stopPropagation()
  }
}