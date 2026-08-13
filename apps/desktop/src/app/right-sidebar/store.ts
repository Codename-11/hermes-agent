import { atom } from 'nanostores'

import { Codecs } from '@/lib/persisted'
import { profilePersistentAtom } from '@/lib/profile-persisted'

export type RightSidebarTabId = 'files' | 'git' | 'terminal' | 'web'

const TAKEOVER_KEY = 'hermes.desktop.terminalTakeover'
const PROFILE_TAKEOVER_KEY = 'hermes.desktop.profileTerminalTakeover.v1'

export const $rightSidebarTab = atom<RightSidebarTabId>('files')
export const $terminalTakeover = profilePersistentAtom({
  codec: Codecs.bool,
  fallback: () => false,
  key: PROFILE_TAKEOVER_KEY,
  legacyKey: TAKEOVER_KEY
})

export const setRightSidebarTab = (tab: RightSidebarTabId) => $rightSidebarTab.set(tab)
export const setTerminalTakeover = (active: boolean) => $terminalTakeover.set(active)

/** A command queued to run in the embedded terminal. The terminal pane flushes
 *  (and clears) it once its session is live, so a value set before the pane
 *  mounts still runs. Cleared after flush so a later remount can't replay it. */
export const $terminalInjection = atom<null | string>(null)

/** Open the terminal pane and run a command in it. Used to disconnect external
 *  (CLI-managed) providers, which Hermes can't clear via the API — the user
 *  sees exactly what runs instead of Hermes silently deleting their creds. */
export const runInTerminal = (command: string) => {
  const trimmed = command.trim()

  if (!trimmed) {
    return
  }

  setTerminalTakeover(true)
  $terminalInjection.set(trimmed)
}
