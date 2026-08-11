import { useStore } from '@nanostores/react'

import { $registryVersion } from '@/contrib/registry'
import { $bindings, bindingsFor } from '@/store/keybinds'

import { KEYBIND_READONLY } from './actions'
import { formatCombo, IS_MAC } from './combo'

const ARIA_KEYS: Record<string, string> = {
  down: 'ArrowDown',
  enter: 'Enter',
  escape: 'Escape',
  left: 'ArrowLeft',
  right: 'ArrowRight',
  space: 'Space',
  tab: 'Tab',
  up: 'ArrowUp'
}

/** WAI-ARIA spelling for the first configured combo (not the visual glyphs). */
export function useKeybindAriaShortcut(actionId: string): string | undefined {
  const bindings = useStore($bindings)
  useStore($registryVersion)
  const combo = bindingsFor(actionId, bindings)[0]

  if (!combo) {
    return undefined
  }

  return combo
    .split('+')
    .map(token => {
      if (token === 'mod') {
        return IS_MAC ? 'Meta' : 'Control'
      }

      if (token === 'ctrl') {
        return 'Control'
      }

      if (token === 'alt') {
        return 'Alt'
      }

      if (token === 'shift') {
        return 'Shift'
      }

      return ARIA_KEYS[token] ?? token.toUpperCase()
    })
    .join('+')
}

// The formatted first combo for `actionId`, or null when unbound. Rebindable
// actions read live from the store; readonly shortcuts (e.g. `composer.steer`)
// fall back to their fixed combo. Returns null for unknown action ids so the
// tooltip shows just the text label with no trailing hint.
export function useKeybindHint(actionId: string): string | null {
  const bindings = useStore($bindings)

  // `bindingsFor`, not a raw `bindings[id]`: $bindings is seeded at module init
  // from the actions known THEN, so a plugin action contributed later isn't in
  // it and a raw lookup renders no hint at all. The resolver falls through to
  // the stored override and the action's own defaults. Subscribing to the
  // registry version repaints the hint when that late registration lands.
  useStore($registryVersion)

  const rebindable = bindingsFor(actionId, bindings)[0]

  if (rebindable) {
    return formatCombo(rebindable)
  }

  const readonly = KEYBIND_READONLY.find(entry => entry.id === actionId)

  if (readonly) {
    return formatCombo(readonly.keys[0])
  }

  return null
}
