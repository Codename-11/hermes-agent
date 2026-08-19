/**
 * SESSION TILES — a stored session rendered as a layout-tree pane BESIDE the
 * main thread (multi-session tiling). A tile IS the real chat surface: the
 * same ChatView/ChatBar/Thread tree the primary session renders, mounted
 * under a tile `SessionView` (its session's slice of `$sessionStates`) and a
 * tile `ComposerScope` (own attachment chips, own focus-bus key). Actions
 * (submit/slash/steer/edit/reload/restore/stop) come from
 * `useSessionTileActions`, all writing through the wiring cache.
 *
 * Lifecycle: `openSessionTile(storedId)` -> `watchSessionTiles` registers a
 * pane contribution docked right of the main zone -> tree adoption lands it
 * -> the pane mounts and asks the delegate for a live runtime id. Closing
 * the pane (tab Close) removes the tile + its zone; tiles persist across
 * restarts and re-resume on boot.
 */

import { useStore } from '@nanostores/react'
import { useQueryClient } from '@tanstack/react-query'
import { atom, computed } from 'nanostores'
import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { useModelControls } from '@/app/session/hooks/use-model-controls'
import { blobToDataUrl } from '@/app/session/hooks/use-prompt-actions/utils'
import { resolveStoredSession } from '@/app/session/hooks/use-session-actions/utils'
import { ModelMenuPanel } from '@/app/shell/model-menu-panel'
import { formatRefValue } from '@/components/assistant-ui/directive-text'
import { CenteredThreadSpinner } from '@/components/assistant-ui/thread/status'
import { usePaneVisible } from '@/components/pane-shell/pane-visibility'
import { findGroupOfPane } from '@/components/pane-shell/tree/model'
import { $layoutTree, closeTreePane, moveTreePane, setTreeGroupHeaderHidden } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { transcribeAudio } from '@/hermes'
import { useI18n } from '@/i18n'
import type { ChatMessage } from '@/lib/chat-messages'
import { NEW_SESSION_TITLE, sessionTitle } from '@/lib/chat-runtime'
import { createComposerAttachmentScope, draftTitleFor } from '@/store/composer'
import { $pinnedSessionIds, pinSession, unpinSession } from '@/store/layout'
import { $activeGatewayProfile, ensureGatewayProfile, normalizeProfileKey } from '@/store/profile'
import { $projectTree } from '@/store/projects'
import { sessionAwaitingInput } from '@/store/prompts'
import {
  $gatewayState,
  $selectedStoredSessionId,
  $sessions,
  sessionMatchesStoredId,
  sessionPinId
} from '@/store/session'
import {
  $sessionStates,
  $sessionTiles,
  closeSessionTile,
  decodeSessionTileKey,
  discardSessionTile,
  patchSessionTile,
  type SessionTile,
  sessionTileDelegate,
  sessionTileKey,
  sessionTileMatches,
  sessionTilePaneId,
  TILE_PANE_PREFIX
} from '@/store/session-states'
import type { SessionInfo } from '@/types/hermes'

import type { SessionDragPayload } from './composer/inline-refs'
import { type ComposerScope, ComposerScopeProvider } from './composer/scope'
import { useComposerActions } from './hooks/use-composer-actions'
import { paneMirror } from './pane-mirror'
import { SessionDraftTitle } from './session-draft-title'
import { startSessionDrag } from './session-drag'
import { SessionStatusDot } from './session-status-dot'
import { useSessionTileActions } from './session-tile-actions'
import { type SessionView, SessionViewProvider } from './session-view'
import { SessionContextMenu } from './sidebar/session-actions-menu'
import { lastVisibleMessageIsUser } from './thread-loading'

import { ChatView } from '.'

const NO_MESSAGES: ChatMessage[] = []

/** The tile's SessionView: the same atom shape the primary chat renders
 *  from, computed from this session's slice of `$sessionStates`. */
function buildTileView(profile: string, storedSessionId: string): SessionView {
  const $runtimeId = computed(
    $sessionTiles,
    tiles => tiles.find(tile => sessionTileMatches(tile, storedSessionId, profile))?.runtimeId ?? null
  )

  const $state = computed([$runtimeId, $sessionStates], (runtimeId, states) =>
    runtimeId ? states[runtimeId] : undefined
  )

  const $messages = computed($state, state => state?.messages ?? NO_MESSAGES)

  return {
    kind: 'tile',
    $awaitingResponse: computed($state, state => Boolean(state?.awaitingResponse)),
    $busy: computed($state, state => Boolean(state?.busy)),
    $cwd: computed($state, state => state?.cwd ?? ''),
    $fast: computed($state, state => Boolean(state?.fast)),
    $lastVisibleIsUser: computed($messages, lastVisibleMessageIsUser),
    $messages,
    $messagesEmpty: computed($messages, messages => messages.length === 0),
    $model: computed($state, state => state?.model ?? ''),
    $profile: atom(normalizeProfileKey(profile)),
    $provider: computed($state, state => state?.provider ?? ''),
    $reasoningEffort: computed($state, state => state?.reasoningEffort ?? ''),
    $runtimeId,
    // Constant for the tile's lifetime — a plain atom, not a computed.
    $storedId: atom(storedSessionId),
    $turnStartedAt: computed($state, state => state?.turnStartedAt ?? null)
  }
}

// Module-level constants so these ChatView props are referentially stable —
// tiles have no pin/delete affordance, and transcription needs no per-tile state.
const noop = () => undefined

const tileTranscribeAudio = async (audio: Blob) =>
  (await transcribeAudio(await blobToDataUrl(audio), audio.type)).transcript

function TileChat({
  profile,
  runtimeId,
  storedSessionId,
  view
}: {
  profile: string
  runtimeId: string
  storedSessionId: string
  view: SessionView
}) {
  const { gateway, requestGateway } = useGatewayRequest()
  const requestOwnerGateway = useCallback(
    async <T,>(method: string, params?: Record<string, unknown>, timeoutMs?: number, signal?: AbortSignal) => {
      await ensureGatewayProfile(profile)

      return requestGateway<T>(method, params, timeoutMs, signal)
    },
    [profile, requestGateway]
  )
  const queryClient = useQueryClient()
  const { selectModel } = useModelControls({ queryClient, requestGateway: requestOwnerGateway })
  const cwd = useStore(view.$cwd)
  const gatewayOpen = useStore($gatewayState) === 'open'

  // One attachment set + focus key per tile, stable for the tile's lifetime.
  const attachments = useRef(createComposerAttachmentScope()).current

  const scope = useMemo<ComposerScope>(
    () => ({
      $awaitingInput: sessionAwaitingInput(runtimeId),
      $messages: view.$messages,
      attachments,
      target: `tile:${sessionTileKey(profile, storedSessionId)}`
    }),
    [attachments, profile, runtimeId, storedSessionId, view.$messages]
  )

  const actions = useSessionTileActions({ profile, runtimeId, scope, storedSessionId })

  // The same attach/pick/paste/drop pipeline the primary composer uses,
  // pointed at this tile's chips + session.
  const composer = useComposerActions({
    activeSessionId: runtimeId,
    currentCwd: cwd,
    requestGateway: requestOwnerGateway,
    scope: {
      add: attachments.add,
      remove: attachments.remove,
      target: scope.target,
      update: attachments.update,
      updateIfCurrent: attachments.updateIfCurrent
    }
  })

  // ChatView is memo()d — every callback prop must be referentially stable or
  // the memo never holds and each tile-level render (idle ticks, unrelated
  // store updates) re-renders the whole chat shell. The individual composer
  // functions are useCallback'd inside useComposerActions, so hoisting these
  // wrappers onto them keeps identity stable across renders.
  const { addContextRefAttachment, pasteClipboardImage, pickContextPaths, pickImages, removeAttachment } = composer

  const onAddUrl = useCallback(
    (url: string) => addContextRefAttachment(`@url:${formatRefValue(url)}`, url),
    [addContextRefAttachment]
  )

  const onPasteClipboardImage = useCallback(
    (opts?: { silent?: boolean }) => pasteClipboardImage(opts),
    [pasteClipboardImage]
  )

  const onPickFiles = useCallback(() => void pickContextPaths('file'), [pickContextPaths])
  const onPickFolders = useCallback(() => void pickContextPaths('folder'), [pickContextPaths])
  const onPickImages = useCallback(() => void pickImages(), [pickImages])
  const onRemoveAttachment = useCallback((id: string) => void removeAttachment(id), [removeAttachment])
  const onRetryResume = useCallback(
    () => patchSessionTile(storedSessionId, { error: undefined }, profile),
    [profile, storedSessionId]
  )

  // Per-tile model menu — rendered under this tile's SessionView so the pill
  // + switch target THIS runtime, not the primary (which may be mid-turn).
  const modelMenuContent = useMemo(
    () =>
      gatewayOpen ? (
        <ModelMenuPanel
          catalogRequestGateway={requestOwnerGateway}
          gateway={gateway || undefined}
          onSelectModel={selectModel}
          profile={profile}
          requestGateway={requestOwnerGateway}
        />
      ) : null,
    [gateway, gatewayOpen, profile, requestOwnerGateway, selectModel]
  )

  return (
    <SessionViewProvider value={view}>
      <ComposerScopeProvider value={scope}>
        <ChatView
          gateway={gateway}
          modelMenuContent={modelMenuContent}
          onAddContextRef={addContextRefAttachment}
          onAddUrl={onAddUrl}
          onAttachDroppedItems={composer.attachDroppedItems}
          onAttachImageBlob={composer.attachImageBlob}
          onAttachPrCommentUrl={composer.attachPrCommentUrl}
          onCancel={actions.cancelRun}
          onDeleteSelectedSession={noop}
          onDismissError={actions.dismissError}
          onEdit={actions.editMessage}
          onPasteClipboardImage={onPasteClipboardImage}
          onPickFiles={onPickFiles}
          onPickFolders={onPickFolders}
          onPickImages={onPickImages}
          onReload={actions.reloadFromMessage}
          onRemoveAttachment={onRemoveAttachment}
          onRestoreToMessage={actions.restoreToMessage}
          onRetryResume={onRetryResume}
          onSteer={actions.steerPrompt}
          onSubmit={actions.submitText}
          onThreadMessagesChange={actions.handleThreadMessagesChange}
          onToggleSelectedPin={noop}
          onTranscribeAudio={tileTranscribeAudio}
        />
      </ComposerScopeProvider>
    </SessionViewProvider>
  )
}

export function SessionTilePane({ tileKey }: { tileKey: string }) {
  const identity = useMemo(() => decodeSessionTileKey(tileKey), [tileKey])
  const tiles = useStore($sessionTiles)
  const visible = usePaneVisible()
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const gatewayOpen = useStore($gatewayState) === 'open'
  const resumingRef = useRef(false)
  const profile = identity?.profile ?? 'default'
  const storedSessionId = identity?.storedSessionId ?? ''
  const tile = identity ? tiles.find(candidate => sessionTileMatches(candidate, storedSessionId, profile)) : undefined
  const runtimeId = tile?.runtimeId ?? null
  const ownerGatewayOpen =
    visible && normalizeProfileKey(activeGatewayProfile) === normalizeProfileKey(profile) && gatewayOpen
  const view = useMemo(() => buildTileView(profile, storedSessionId), [profile, storedSessionId])

  const activateOwner = useCallback(() => {
    if (identity) {
      void ensureGatewayProfile(profile)
    }
  }, [identity, profile])

  // Only a pane becoming visible activates its owner. Keep-alive mounted hidden
  // tabs do not subscribe to profile changes and therefore cannot create a
  // profile-switch loop between split/tab surfaces.
  useEffect(() => {
    if (visible) {
      activateOwner()
    }
  }, [activateOwner, visible])

  const hasMessages = useStore(view.$messagesEmpty) === false

  useEffect(() => {
    const alreadyListed = () =>
      $sessions
        .get()
        .some(
          session =>
            sessionMatchesStoredId(session, storedSessionId) &&
            normalizeProfileKey(session.profile) === normalizeProfileKey(profile)
        )

    if (!ownerGatewayOpen || !runtimeId || !hasMessages || alreadyListed()) {
      return
    }

    let cancelled = false
    let timer: number | undefined

    const attempt = (remaining: number) => {
      if (cancelled || alreadyListed()) {
        return
      }

      void resolveStoredSession(storedSessionId, profile)
        .then(resolved => {
          if (cancelled || resolved || remaining <= 0) {
            return
          }

          timer = window.setTimeout(() => attempt(remaining - 1), 500)
        })
        .catch(() => undefined)
    }

    attempt(6)

    return () => {
      cancelled = true

      if (timer !== undefined) {
        window.clearTimeout(timer)
      }
    }
  }, [hasMessages, ownerGatewayOpen, profile, runtimeId, storedSessionId])

  // eslint-disable-next-line no-restricted-syntax -- process-local in-flight lock, not mirrored atom state
  useEffect(() => {
    if (!ownerGatewayOpen || runtimeId || tile?.error || resumingRef.current || !identity) {
      return
    }

    const delegate = sessionTileDelegate()

    if (!delegate) {
      return
    }

    resumingRef.current = true

    delegate
      .resumeTile(storedSessionId, profile)
      .then(id => patchSessionTile(storedSessionId, { error: undefined, runtimeId: id }, profile))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err)

        if (/session not found|\b404\b/i.test(message)) {
          discardSessionTile(storedSessionId, profile)
        } else {
          patchSessionTile(storedSessionId, { error: message }, profile)
        }
      })
      .finally(() => {
        resumingRef.current = false
      })
  }, [identity, ownerGatewayOpen, profile, runtimeId, storedSessionId, tile?.error])

  // Clear a stale resume error only on a real gateway/profile edge. Depending
  // on tile.error here creates an infinite retry loop: resume rejects → set
  // error → this effect clears it → resume runs again.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- tile.error is deliberately latched until an edge or explicit Retry
  useEffect(() => {
    if (ownerGatewayOpen && tile?.error) {
      patchSessionTile(storedSessionId, { error: undefined }, profile)
    }
  }, [ownerGatewayOpen, profile, storedSessionId])

  let content: React.ReactNode

  if (tile?.error) {
    content = (
      <div className="grid h-full place-items-center p-4">
        <div className="max-w-[24rem] space-y-2 text-center font-mono text-[11px]">
          <div className="text-(--ui-danger,#f87171)">Could not open this session</div>
          <div className="break-words text-(--ui-text-quaternary)">{tile.error}</div>
          <Button
            onClick={() => patchSessionTile(storedSessionId, { error: undefined }, profile)}
            size="sm"
            variant="outline"
          >
            Retry
          </Button>
        </div>
      </div>
    )
  } else if (!runtimeId) {
    content = (
      <div className="relative h-full">
        <CenteredThreadSpinner />
      </div>
    )
  } else {
    content = <TileChat profile={profile} runtimeId={runtimeId} storedSessionId={storedSessionId} view={view} />
  }

  return (
    <div className="h-full" onFocusCapture={activateOwner} onPointerDownCapture={activateOwner}>
      {content}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Tile -> pane contribution sync (call once from the app root).
// ---------------------------------------------------------------------------

/** Resolve a tile's stored row: the recents list first, then the project
 *  tree. A session opened as a tab from a project group is often older than
 *  the paginated recents page, so it has no `$sessions` row at all until new
 *  activity lands it there — resolving through the tree keeps its tab titled
 *  and tinted instead of a grey "Session" placeholder. */
export function tileStoredRow(storedSessionId: string, profile: string): SessionInfo | undefined {
  const match = (session: SessionInfo) =>
    sessionMatchesStoredId(session, storedSessionId) &&
    normalizeProfileKey(session.profile) === normalizeProfileKey(profile)

  return (
    $sessions.get().find(match) ??
    $projectTree
      .get()
      .flatMap(project => [
        ...project.repos.flatMap(repo => repo.groups.flatMap(group => group.sessions)),
        ...(project.previewSessions ?? [])
      ])
      .find(match)
  )
}

const tileIdentity = (key: string) => decodeSessionTileKey(key) ?? { profile: 'default', storedSessionId: key }

function tileTitle(key: string): string {
  const identity = tileIdentity(key)
  const stored = tileStoredRow(identity.storedSessionId, identity.profile)

  return stored ? sessionTitle(stored) : NEW_SESSION_TITLE
}

function tileDragPayload(key: string): SessionDragPayload {
  const identity = tileIdentity(key)
  const stored = tileStoredRow(identity.storedSessionId, identity.profile)
  const title = stored ? sessionTitle(stored) : draftTitleFor(key) || NEW_SESSION_TITLE

  return { id: identity.storedSessionId, profile: identity.profile, title }
}

// Qualified tile key awaiting close confirmation (null = no dialog).
const $confirmCloseTile = atom<null | string>(null)

export function requestCloseSessionTile(key: string): void {
  const identity = tileIdentity(key)
  const tile = $sessionTiles
    .get()
    .find(candidate => sessionTileMatches(candidate, identity.storedSessionId, identity.profile))
  const state = tile?.runtimeId ? $sessionStates.get()[tile.runtimeId] : undefined

  if (state?.busy || state?.awaitingResponse || state?.needsInput) {
    $confirmCloseTile.set(key)
  } else {
    closeSessionTile(identity.storedSessionId, identity.profile)
  }
}

export function SessionTileCloseConfirm() {
  const { t } = useI18n()
  const key = useStore($confirmCloseTile)

  return (
    <ConfirmDialog
      confirmLabel={t.zones.closeRunningConfirm}
      description={t.zones.closeRunningBody}
      destructive
      onClose={() => $confirmCloseTile.set(null)}
      onConfirm={() => {
        if (key) {
          const identity = tileIdentity(key)
          closeSessionTile(identity.storedSessionId, identity.profile)
        }
      }}
      open={key !== null}
      title={t.zones.closeRunningTitle}
    />
  )
}

export function stackSessionTilesIntoMain(): void {
  for (const tile of $sessionTiles.get()) {
    const tree = $layoutTree.get()
    const mainGroup = tree ? findGroupOfPane(tree, 'workspace')?.id : null

    if (mainGroup) {
      moveTreePane(sessionTilePaneId(tile.profile, tile.storedSessionId), { groupId: mainGroup, pos: 'center' })
    }
  }
}

function useTileMenuRow(
  storedSessionId: string,
  ownerProfile?: string
): { pinId: string; profile: string; title: string } {
  const cache = useRef<{ key: string; value: { pinId: string; profile: string; title: string } } | null>(null)

  const subscribe = useCallback((onChange: () => void) => {
    const offSessions = $sessions.listen(onChange)
    const offTree = $projectTree.listen(onChange)

    return () => {
      offSessions()
      offTree()
    }
  }, [])

  return useSyncExternalStore(subscribe, () => {
    const profile = normalizeProfileKey(ownerProfile ?? $activeGatewayProfile.get())
    const stored = tileStoredRow(storedSessionId, profile)
    const pinId = stored ? sessionPinId(stored) : storedSessionId
    const title = stored ? sessionTitle(stored) : NEW_SESSION_TITLE
    const key = pinId + '\u0000' + title + '\u0000' + profile

    if (cache.current?.key !== key) {
      cache.current = { key, value: { pinId, profile, title } }
    }

    return cache.current.value
  })
}

export function SessionTabMenu({
  children,
  onClose,
  onHideTabBar,
  ownerProfile,
  storedSessionId,
  tabPaneId
}: {
  children: React.ReactElement
  onClose?: () => void
  onHideTabBar?: () => void
  ownerProfile?: string
  storedSessionId: string
  tabPaneId: string
}) {
  const { pinId, profile, title } = useTileMenuRow(storedSessionId, ownerProfile)
  const pinnedSessionIds = useStore($pinnedSessionIds)
  const pinned = pinnedSessionIds.includes(pinId)

  return (
    <span className="contents" onContextMenu={event => event.stopPropagation()}>
      <SessionContextMenu
        onArchive={() => void sessionTileDelegate()?.archiveSession(storedSessionId, profile)}
        onBranch={() => void sessionTileDelegate()?.branchSession(storedSessionId, profile)}
        onClose={onClose}
        onDelete={() => void sessionTileDelegate()?.deleteSession(storedSessionId, profile)}
        onHideTabBar={onHideTabBar}
        onPin={() => (pinned ? unpinSession(pinId) : pinSession(pinId))}
        onReload={
          tabPaneId.startsWith(TILE_PANE_PREFIX)
            ? () => sessionTileDelegate()?.rehydrateTile(storedSessionId, profile)
            : undefined
        }
        pinned={pinned}
        profile={profile}
        sessionId={storedSessionId}
        surface="tab"
        tabPaneId={tabPaneId}
        title={title}
      >
        {children}
      </SessionContextMenu>
    </span>
  )
}

/** The main tab retains its existing hide/show context-menu behavior. */
export function WorkspaceTabMenu({ children }: { children: React.ReactElement }) {
  const selected = useStore($selectedStoredSessionId)
  const profile = useStore($activeGatewayProfile)

  const hideTabBar = () => {
    const tree = $layoutTree.get()
    const group = tree ? findGroupOfPane(tree, 'workspace') : null

    if (group) {
      setTreeGroupHeaderHidden(group.id, true)
    }
  }

  if (!selected) {
    return children
  }

  return (
    <SessionTabMenu
      onClose={() => closeTreePane('workspace')}
      onHideTabBar={hideTabBar}
      ownerProfile={profile}
      storedSessionId={selected}
      tabPaneId="workspace"
    >
      {children}
    </SessionTabMenu>
  )
}

export const watchSessionTiles = paneMirror<SessionTile>({
  source: $sessionTiles,
  also: [$sessions, $projectTree],
  key: tile => sessionTileKey(tile.profile, tile.storedSessionId),
  prefix: 'session-tile',
  dir: tile => tile.dir,
  anchor: tile => tile.anchor,
  before: tile => tile.before,
  minWidth: '20rem',
  title: tileTitle,
  tabLead: key => {
    const identity = tileIdentity(key)

    return (
      <SessionStatusDot
        session={tileStoredRow(identity.storedSessionId, identity.profile)}
        storedSessionId={identity.storedSessionId}
      />
    )
  },
  tabTitle: key => {
    const identity = tileIdentity(key)

    return tileStoredRow(identity.storedSessionId, identity.profile) ? null : <SessionDraftTitle scope={key} />
  },
  render: key => <SessionTilePane tileKey={key} />,
  tabWrap: (key, tab) => {
    const identity = tileIdentity(key)

    return (
      <SessionTabMenu
        onClose={() => requestCloseSessionTile(key)}
        ownerProfile={identity.profile}
        storedSessionId={identity.storedSessionId}
        tabPaneId={sessionTilePaneId(identity.profile, identity.storedSessionId)}
      >
        {tab}
      </SessionTabMenu>
    )
  },
  tabDrag: (key, event, onTap, double) => {
    startSessionDrag(tileDragPayload(key), event, { double, onTap })

    return true
  },
  close: requestCloseSessionTile
})
