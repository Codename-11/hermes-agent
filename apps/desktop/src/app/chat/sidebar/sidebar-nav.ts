import type { SidebarNavContribution } from '../../routes'
import type { SidebarNavItem } from '../../types'

export interface ResolvedSidebarNavContribution {
  codicon: string
  label: string
  onSelect?: () => void
  route?: string
}

export function resolveSidebarNavContribution(
  data: unknown
): ResolvedSidebarNavContribution | null {
  if (!data || typeof data !== 'object') {
    return null
  }

  const candidate = data as Partial<SidebarNavContribution>
  const label = typeof candidate.label === 'string' ? candidate.label.trim() : ''
  const onSelect = typeof candidate.onSelect === 'function' ? candidate.onSelect : undefined
  const route = candidate.path?.startsWith('/') ? candidate.path : undefined

  if (!label || (!route && !onSelect)) {
    return null
  }

  return {
    codicon: candidate.codicon || 'plug',
    label,
    onSelect,
    route
  }
}

export function activateSidebarNavItem(
  item: SidebarNavItem,
  navigate: (item: SidebarNavItem) => void
): void {
  if (item.onSelect) {
    item.onSelect()

    return
  }

  navigate(item)
}
