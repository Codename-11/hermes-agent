# Axiom Hermes Fork Contract

Repo: `/home/bailey/.hermes/hermes-agent`  
Canonical upstream: `upstream/main`  
Deploy artifact: `origin/axiom`  
Live branch: `axiom`

This file records the protected behavior and historical inventory for the Axiom-maintained Hermes fork. The concise operator-facing contract for Docker-Server + Axiom-Desktop lives in `docs/axiom-fork-contract.md`.

Do **not** treat generated counts or old commit inventories in this file as live status. For current state, run:

```bash
cd /home/bailey/.hermes/hermes-agent
scripts/fork-status.py          # read-only local status
scripts/fork-status.py --fetch  # optional read-only fetch before reporting
scripts/fork-status.py --desktop # optional read-only Axiom-Desktop SSH probe
```

## Current fork state

Live state is intentionally generated on demand by `scripts/fork-status.py` so branch counts do not rot in docs. The status helper reports:

- current local branch and dirty files;
- `HEAD`, `origin/axiom`, `upstream/main`, and local `main` heads;
- `axiom...origin/axiom`, `origin/axiom...upstream/main`, and `main...upstream/main` counts;
- whether `origin/axiom` / `HEAD` contain `upstream/main`;
- Sentinel `Hermes Axiom Sync` cron enabled/paused state;
- optional Axiom-Desktop branch state through read-only SSH when reachable.

Operational note: as of 2026-06-17, the `agent/anthropic_adapter.py` / `agent/system_prompt.py` upstream merge conflict was resolved on `axiom`. The old Axiom `/personality` hunk in `agent/system_prompt.py` was fully dropped in favor of upstream's dynamic `ephemeral_system_prompt` injection in `agent/conversation_loop.py` and `agent/chat_completion_helpers.py`; do not reintroduce the stable-prompt override unless a focused repro shows upstream has regressed.

## Rules for upstream merge resolution

1. **Never resolve conflicts by taking all upstream or all fork changes in protected files.** Protected behavior below must be explicitly preserved, retired, or replaced.
2. **Resolve from a fresh worktree based on `origin/axiom`, not the live checkout, unless the live checkout has first been fast-forwarded intentionally.**
3. **Before pushing a resolved merge to `origin/axiom`, run the fork contract tests listed in this file.** Add missing tests before trusting manual review.
4. **When upstream has refactored a hotspot file, port Axiom behavior into upstream's new split/module rather than re-expanding the old god file.**
5. **If a feature is obsolete because upstream now provides equivalent behavior, mark it retired in this file and add verification evidence.**
6. **Do not resume the daily sync cron until conflict alerts are deduped and the contract tests cover the protected Axiom behavior.**

## Axiom Desktop session convergence and profile-safe live status

Axiom carries a drop-ready Desktop stability layer for concurrent chats and
multiple gateway profiles. Source carries: `6616b11bef`, `7d37b0ef4a`,
`5705aa6ab8`, and `4510638d86`. The steer-ordering carry is a narrow extension
of upstream PR `#69739`, not a replacement steering subsystem.

The protected contract is:

- request retry, reconnect, transcript/todo hydration, and gateway events stay
  bound to the profile/gateway/runtime that originated them; mutable foreground
  selection must never retarget older asynchronous work;
- persisted chat identity remains separate from runtime identity, and both live
  status and unscoped stream routing are profile-qualified so cloned profiles
  may safely contain the same stored session id;
- reconnect reconciliation queries `session.active_list` on the exact gateway
  that reconnected, including background secondary gateways, and updates the
  cache-owned canonical runtime state—not only the sidebar projection;
- an idle or vanished authoritative runtime fully settles streaming state
  (`busy`, `awaitingResponse`, `needsInput`, stream/turn/interim/adoption fields,
  and pending messages), while a captured state baseline prevents an older
  snapshot from overwriting a newer local stream edge;
- delayed transcript/todo hydration and messaging polls are ownership and
  generation fenced, preserving optimistic or streaming work created after the
  request began;
- a warm cached runtime accepted by the primary pane is made active and
  synchronously republished before `session.activate` is awaited; the public
  `$sessionStates` slice may have been evicted while idle even though the private
  runtime cache remains valid, and `PRIMARY_SESSION_VIEW` cannot rehydrate from
  the legacy global message mirror alone; an empty private cache is never valid
  warm transcript authority for a stored session—sidebar `message_count` and
  `is_active` are asynchronous projections and cannot prove emptiness, so the
  primary window must take the full resume path and recover authoritative
  history/live projection instead;
- the same empty-cache rule applies to session tiles even while a backend turn
  is running: `busy` proves activity, not transcript ownership, so no empty tile
  cache may short-circuit `resumeTile`; tile cold resume grafts the gateway's
  `inflight`/`queued` projection onto bounded REST history, and the existing
  tab-menu Reload discards only that renderer's tile binding/cache before
  remounting and profile-safe rehydrate without interrupting the backend turn;
- live steer projection preserves causal user ordering even when reconciliation
  briefly places an active assistant shell before the original optimistic user
  prompt: keep upstream's stable `original → steer → reply` redirect ordering,
  but advance the steer boundary past user rows already visible at or after the
  shell so a later correction never paints above the prompt that started the
  turn; build-window queued redirects may still move to the tail after the RPC
  classifies them as the next turn;
- `session.reclaimed` atomically evicts public state, private runtime state,
  reverse stored/runtime mappings, todos, and active bindings; and
- applying a non-primary profile connection notifies the renderer so its
  profile-owned WebSocket is recreated against the new backend.

Primary protected files: `apps/desktop/src/app/contrib/{wiring.tsx,hooks/
use-background-sync.ts}`, `apps/desktop/src/app/gateway/hooks/{use-gateway-boot,
use-gateway-request}.ts`, `apps/desktop/src/app/session/hooks/{use-session-state-cache,
use-session-actions/{index,utils},use-message-stream/{index,gateway-event}}.ts`,
`apps/desktop/src/store/{gateway,live-session-status,live-sync,session-states,
session-dot-state}.ts`, `apps/desktop/electron/connection-apply.ts`, and their
focused tests/bridge declarations.

Focused verification:

```bash
cd apps/desktop
NODE_ENV=test npm run test:ui -- \
  src/app/contrib/hooks/live-status-reap.test.ts \
  src/app/gateway/hooks/use-gateway-boot.test.tsx \
  src/app/gateway/hooks/use-gateway-request.test.ts \
  src/app/session/hooks/use-message-stream/interim-sealing.test.tsx \
  src/app/session/hooks/use-message-stream/session-reclaimed.test.tsx \
  src/app/session/hooks/use-session-actions.test.tsx \
  src/app/session/hooks/use-session-state-cache.test.tsx \
  src/store/live-sync.test.ts \
  src/store/session-dot-state.test.ts
NODE_ENV=test npm run test:desktop:platforms -- electron/connection-apply.test.ts
npm run typecheck
```

Related upstream work includes PRs `#45653`, `#69739`, and `#71475` plus issue `#51058`,
but those references cover narrower reconnect symptoms. Drop this carry only
after upstream provides equivalent cache ownership, stale-async fencing,
secondary-profile reconciliation, reclaim eviction, and profile-qualified
status/event routing, including rejection of empty warm transcript caches across
windows and stale sidebar metadata, plus causal steer ordering for both stable
and temporarily non-canonical live tails, with the focused invariants above
still passing.

## Axiom Desktop update-service plugin seam

The source-owned, opt-in **Axiom Enhancements** disk plugin lives in the private
Axiom Agent Library; Hermes core does not bundle Axiom-specific renderer UI. Its
first module, Update Control, is a singleton main-pane tab rather than a workspace
route. It must stack with session tabs, participate in the standard tab
menu/keyboard lifecycle, and remain explicitly closeable. Closing dismisses only
`axiom-enhancements:panel`; it must not disable the plugin or remove its sidebar,
status-bar, and command-palette reopen actions. Contribution-registry refreshes
must preserve the explicit dismissal until one of those actions runs.

Its Hermes upstream history is a compact semantic table rather than an
unbounded commit list. Scope filtering preserves newest-first order, renders 25
commits per page by default, and returns to page one when the filter or refreshed
commit payload changes.

The reusable SDK seam is plugin-scoped: a pane opts into
`data: { closeBehavior: 'dismiss' }`, and `ctx.panes.reveal(localId)` restores,
unhides, and focuses the namespaced pane. Generic plugin panes retain the
existing close-to-disable behavior unless they explicitly opt in.

The existing singleton in-app Browser remains directly reachable from the
sidebar. Its row re-fronts the current URL pane without navigating it away, or
creates that same singleton at its home page when none exists. Do not add a
parallel browser surface.

### Staged update lifecycle

On Windows, Update Control must prepare release updates without mutating the live checkout, venv, or packaged app. Preparation fetches and pins the deploy target, builds the Desktop package in a Hermes-owned isolated worktree, records artifact integrity and the live checkout fingerprint, and atomically publishes a stage manifest. Closing the Update Control pane must not cancel or duplicate that worker.

Restart/apply must revalidate the exact branch, live HEAD, dirty fingerprint, remote target, Hermes-owned paths, build stamp, and artifact hash **before** releasing the backend lock or quitting Electron. A failed preflight leaves the current Desktop and relay available. The detached handoff may then adopt the staged package, run the normal deploy-aware CLI updater, and restore the previous packaged app on failure.

Plugins may control this lifecycle only through the typed `host.updates` methods (`refresh`, `syncUpstream`, `getUpstreamSyncStatus`, `getStage`, `prepare`, `discardStage`, `restartAndApply`, `standardUpdate`, and `getHistory`). `standardUpdate` is the explicit non-staged escape hatch: after confirmation it reuses the existing guarded, deploy-aware Desktop apply flow, releases backend locks, launches the detached normal Hermes updater, and closes Desktop only after that handoff succeeds. A launch failure or manual-command fallback must keep Desktop and the confirmation open with an actionable error. Do not expose raw IPC, shell execution, arbitrary manifest paths, branch mutation, or the legacy immediate `apply` method to plugins.

`syncUpstream` is a distinct **Hermes upstream → Axiom deploy** operation. It reuses the deploy-aware `hermes update` reconciliation and guarded resolver in a Hermes-owned isolated worktree, publishes the verified result to `origin/<deploy>`, and must not mutate live `HEAD`, rewrite local `main`, rebuild Desktop, or restart services. A retained conflict handoff remains resumable through the same action even when upstream divergence has since reached zero. Preparing/applying **Axiom deploy → Local** stays a separate explicit lifecycle; a prepared local stage blocks upstream sync because publishing a new deploy target would invalidate that artifact.

The resolver child owns edits and cheap structural checks only. The parent rejects unexpected untracked files, stages tracked changes only, commits a checkpoint, and binds all later validation and publication to that exact full `HEAD`. For each conflict, the updater derives a bounded resolver brief under `update-reports/` containing only matched watch areas, conflicting files, protected invariants, upstream/drop guidance, precise references, and parent-owned check IDs. The child reads that brief instead of loading the full fork contract and operations corpus every run. Marker phases retain `resolved_head`, `validation_sha`, and a typed check ledger keyed by the full resolved SHA. Each result carries the stable check ID, canonical-spec SHA-256 fingerprint, status, nullable return code, bounded/redacted output tail, duration, and completion time. Passed results are reused only when both SHA and fingerprint match; old command-keyed `check_status` maps are accepted but not trusted for reuse. Parent checks run serially, with Python checks before Desktop checks; Desktop dependencies are prepared once inside the retained worktree with an isolated, script-free `npm ci`. When a real compile/test check fails, the parent changes the handoff to `repair_pending` and immediately feeds the bounded failed-check diagnostics back to the resolver for at most two tracked-source repair passes in the same updater invocation. Every repair is checkpointed to a new SHA and revalidated by the parent; dependency-preparation/environment failures are never delegated to the LLM. A resolver timeout terminates the process tree, then salvages the work only when structural validation succeeds; otherwise the handoff remains `resolve_pending`.

If deploy resolution and parent validation succeed but the final push fails, the
handoff advances to `push_pending` instead of returning to conflict resolution.
Persist the exact full commit ID plus a bounded, redacted Git diagnostic tail,
retain the worktree, and let the next update retry that exact commit without
launching the resolver agent again. If the retained HEAD changed, stop safely.
The CLI must also print a focused fresh-chat command for auth, remote divergence,
hook rejection, or network diagnosis; it must not auto-nest another agent session.

Electron main serializes sync, prepare, discard, legacy apply, and restart/apply through one update-operation coordinator. Renderer disabled states are only UX; the main-process coordinator is the correctness boundary. Detached preparation must publish its existing `.prepare-lock` ownership before the coordinator releases the launch operation, after which the on-disk stage lock/status remains authoritative. The upstream subprocess has a bounded deadline, terminates its full process tree, rejects nonzero exits even if output contained a success payload, and clears ownership after every completion/failure so explicit retry remains possible.

Upstream reconciliation output is observable while that exclusive operation is still running. Electron owns a bounded, sanitized in-memory snapshot and exposes it through a read-only status handler outside the mutation coordinator; the renderer polls only while `running` is true. Update Control must open the Reconcile CLI output disclosure immediately on Sync, show a waiting placeholder before the first byte, update it as output arrives, survive pane remounts, and retain the final transcript/result after exit.

Persisted preparation percentages are not proof of active work. Electron must reconcile every nonterminal `progress.json` phase with the `.prepare-lock` owner before returning status to the renderer. A live owner preserves preparing state; a dead or missing owner with no ready manifest becomes a terminal interrupted failure, optionally noting when a newer Desktop build superseded it. Preserve logs and diagnostics for discard/retry, but never replay orphaned progress indefinitely after restart.

Update Control exposes preparation activity only from authoritative Electron status checks. While a verified worker owns the stage lock, poll that status at a bounded interval, show a spinner plus last-check freshness, and offer cancellation. Cancellation must re-verify the PID command line against the exact Hermes stage script and stage root, terminate that worker's process tree, preserve a terminal cancelled result/history entry, and leave staged files available for explicit discard. Never offer cancellation for ready, replacement, apply, or restart phases.

Keep checking and mutation actions explicit. **Check Desktop** refreshes the local/deploy/upstream lineage and remains available for a ready stage; **Check Backend** refreshes the connected backend independently. A ready Desktop stage may be replaced through **Discard & check latest**, after which upstream publication and local preparation remain separate actions. Upstream → Axiom publication is one shared deploy operation shown on the Desktop lane, not a duplicated backend action, and any retained/prepared stage blocks publication until discarded.

The staged Windows handoff owns exactly one visible update surface. Keep the proven detached `cmd start` wrapper, but launch PowerShell with `-WindowStyle Hidden` so its required console never appears beside the script-owned updater window. That window is a normal taskbar surface with Minimize enabled, Maximize disabled, no permanent always-on-top pin, phase-oriented status copy, and technical CLI output as secondary diagnostics. Closing is blocked while the update is active; minimizing must not interrupt the updater, its log, rollback, result, or relaunch behavior.

Update history is the bounded Hermes-owned `logs/update-history.json` index. Successful CLI updates retain Markdown briefs plus structured sidecars; failed preparation and detached apply results are reconciled into the same index. Update Control presents categorized pending commits, staged progress/recovery, and completed/failed history from this contract.

Every selected install (Desktop client or connected backend) must expose the update lineage as three distinct authorities: **Hermes upstream** (`upstream/main`) → **Axiom** (`origin/<deploy>`) → **Local** (the running checkout). The status contract keeps deploy commits (`Local..Axiom`) separate from upstream commits (`Axiom..upstream/main`); Update Control renders both ranges independently so upstream activity is readable at a glance without double-counting deploy-only work.

The plugin renderer source and generated disk artifact are protected in
`axiom-agent-library/plugins/desktop/axiom-enhancements/`. Core protected files:
`apps/desktop/electron/main.ts`,
`apps/desktop/electron/update-handoff-status.ts`,
`apps/desktop/electron/update-operation-coordinator.ts`,
`apps/desktop/electron/updater-process.ts`,
`apps/desktop/electron/upstream-sync.ts`,
`apps/desktop/src/global.d.ts`,
`apps/desktop/src/sdk/index.ts`,
`apps/desktop/src/store/updates.ts`,
`apps/desktop/src/types/hermes.ts`,
`hermes_cli/web_server.py`,
`apps/desktop/src/contrib/plugin.ts`,
`apps/desktop/src/components/pane-shell/tree/store.ts`,
`apps/desktop/src/app/chat/sidebar/index.tsx`,
`apps/desktop/src/store/preview.ts`, and
`apps/desktop/src/store/session-states.ts`, plus the publish-only path in
`hermes_cli/axiom_update.py`. Focused verification:

```bash
cd apps/desktop
NODE_ENV=test npx vitest run --environment jsdom \
  electron/update-handoff-status.test.ts \
  electron/update-operation-coordinator.test.ts \
  electron/updater-process.test.ts \
  electron/upstream-sync.test.ts \
  src/contrib/plugin.test.ts \
  src/components/pane-shell/tree/pane-toggle-visibility.test.ts \
  src/store/preview.test.ts \
  src/store/session-states.test.ts
npm run typecheck
cd ../..
python3 -m pytest -o addopts= -q tests/hermes_cli/test_update_autostash.py
```

Core drop condition: upstream provides equivalent typed updater capabilities and
a reopenable plugin main-tab lifecycle where tab Close does not disable the
plugin and explicit plugin entry points can restore/focus the same singleton
pane. The Axiom-specific renderer remains an Agent Library plugin until its
operator workflow is no longer needed.

## Axiom Desktop voice keybinds

Desktop carries three independent, user-rebindable composer actions:
`composer.dictate` toggles one-shot dictation in exactly the active visible
composer, `composer.autoSpeak` persists a profile-qualified setting for exactly
the active conversation's durable lineage id, and `composer.wakeWord`
delegates to the gateway-owned wake listener. Dictation ships on
`mod+shift+d` so it can be captured while the composer owns focus; existing
bare/Shift-only dictation overrides migrate to that default, and the keybind
editor refuses to save a dictation chord without a primary Ctrl/Cmd modifier.
Auto-speak and wake word remain unbound. All three must remain separate from
`composer.voice` (the full voice-conversation toggle), and Desktop's keybind
registry—not `voice.record_key`—is authoritative.

Source carry for conversation-owned read-aloud: `633be55974`. This extends the
existing upstream voice/composer path; it does not introduce a second playback
engine or redefine gateway-wide messaging `voice.auto_tts`.

Dictation routing must retain the composer event-bus ownership filter so tiled
composers do not all record. Wake shortcuts must preserve backend mic ownership,
pending/capture state, persisted config truth, and surface refusals or failures
as visible notifications. The three composer buttons must display live configured
bindings and expose WAI-ARIA `aria-keyshortcuts` values.

Protected files: `apps/desktop/src/lib/keybinds/`,
`apps/desktop/src/app/hooks/use-keybinds.ts`,
`apps/desktop/src/app/chat/composer/focus.ts`, composer voice hooks/controls,
voice preference/wake stores, and Desktop i18n labels. Focused verification:

```bash
cd apps/desktop
npx vitest run --project ui \
  src/lib/keybinds/voice-actions.test.ts \
  src/store/keybinds-dictation.test.ts \
  src/app/hooks/use-keybinds.test.ts \
  src/app/chat/composer/focus.test.ts \
  src/app/chat/composer/controls.test.tsx \
  src/store/voice-prefs.test.ts \
  src/store/wake-word.test.ts
npm run typecheck
```

Drop condition: upstream provides equivalent distinct rebindable actions with
active-composer dictation routing, profile + durable-conversation-lineage
read-aloud ownership enforced at the playback trigger (including tiles and
runtime-id collisions), gateway-authoritative wake behavior with keyboard-visible
failures, and matching tooltip/accessibility discovery.

## Axiom Desktop Terminal shortcut and startup delivery

Desktop's built-in navigation rail exposes an idempotent **Terminal** shortcut
beside **Browser**. It restores/focuses the existing singleton Terminal pane via
the pane tree; it must not toggle an already visible pane closed or create a
parallel terminal surface.

Embedded PTY startup uses an explicit renderer-subscription barrier. The main
process buffers ordered data/exit events emitted after spawn but before
`terminal:start` returns its generated session id; after preload has attached
the corresponding session-scoped IPC listeners, `terminal:subscribe` flushes
that startup buffer exactly once and switches to live delivery. This is required
for SSH/remote startup, where a prompt or immediate connection failure can
otherwise be emitted before the renderer knows which channel to observe, leaving
a live or failed Terminal tab visually blank.

Protected files: `apps/desktop/electron/{main,preload,terminal-output-relay}.ts`,
`apps/desktop/src/global.d.ts`,
`apps/desktop/src/app/right-sidebar/terminal/use-terminal-session.ts`, and
`apps/desktop/src/app/chat/sidebar/index.tsx`. Focused verification:

```bash
cd apps/desktop
npm run test:desktop:platforms -- \
  electron/terminal-output-relay.test.ts \
  electron/connection-apply.test.ts \
  electron/windows-remote-lifecycle.test.ts
NODE_ENV=test npm run test:ui -- \
  src/components/pane-shell/tree/tool-pane-toggle.test.ts \
  src/app/right-sidebar/terminal/persistent.test.tsx \
  src/app/right-sidebar/terminal/terminals.test.ts
npm run typecheck
```

Drop condition: upstream provides both an equivalent idempotent built-in
Terminal navigation entry and lossless ordered PTY startup delivery after the
renderer has attached session-scoped listeners, including prompt output and
early exits over SSH/remote startup.

## Axiom shared cron registry and generic profile ownership

Axiom keeps one inspectable cron registry at the platform root (`<root>/cron/jobs.json`) while every profile-scoped row carries `owner_profile` / `profile` metadata. This is a storage and management carry only; execution must remain generic:

- root or missing ownership normalizes to the synthetic `default` profile;
- every valid named owner—including a profile literally named `victor`—remains that named profile;
- scripts resolve under the owner's `<profile-home>/scripts/` and receive that home as `HERMES_HOME`;
- agent jobs load the owner profile's runtime home when the dispatching ticker differs;
- the shared tick lock remains under the platform root so multiple profile gateways cannot race the same registry.

Do not add agent-name aliases to `cron/jobs.py`; deployment-specific profile names belong in host state, not core normalization. Protected files: `cron/jobs.py`, `cron/scheduler.py`, `tests/cron/test_cron_profile_storage.py`, and `tests/cron/test_cron_profile_isolation.py`. Focused verification:

```bash
python -m pytest -q -o addopts='' \
  tests/cron/test_cron_profile_storage.py \
  tests/cron/test_cron_profile_isolation.py \
  tests/cron/test_cron_script.py
```

Drop condition: upstream provides a cross-profile shared registry with equivalent owner isolation, owner-home script execution, and a root-shared tick lock.

## Dashboard chat profile-scoped PTY attachments

Dashboard Chat must never reattach a selected named profile to a PTY spawned under another profile. The frontend's keep-alive attachment token is therefore scoped by the profile selector (`current`, `victor`, etc.); the backend registry intentionally treats attachment tokens as opaque and cannot repair a cross-profile collision after the fact.

Protected files: `web/src/pages/ChatPage.tsx`, `web/src/lib/pty-attach-token.ts`, and `web/src/lib/pty-attach-token.test.ts`. Focused verification:

```bash
cd web
npm test -- --run src/lib/pty-attach-token.test.ts
npm run typecheck
```

Drop condition: upstream scopes dashboard PTY keep-alive identity by profile, or the backend binds and validates every attachment token against immutable profile metadata.

## Axiom Desktop hybrid Projects overview and typed project paths

Axiom carries a focused Desktop information-architecture and session-ownership
layer while upstream's Projects UX remains in flux. Source carry for pinned
remote-profile discovery: `001f984b5c`.

- Projects stay the primary lane in grouped mode, followed by a separate flat
  **Recent Sessions** lane in the same overview. The recent lane reuses the
  existing session rows, actions, date/status grouping, filters, ordering, and
  pagination. A Project/Home chevron independently expands a persisted,
  bounded five-session preview; the Project label still opens full drill-in and
  the preview ends with a labeled `View all N sessions` action. The active
  conversation must remain in its preview even when it is older than the five
  most recent rows.
- Project membership is computed from one complete compact eligible-session
  query (`limit=-1`) before presentation limits are applied. The five-row
  preview is hydration/UI policy only; it must never decide whether an older
  session belongs to a Project or Home.
- Projects/Home and flat recents share the same source posture: only known
  interactive local conversations enter project navigation. Messaging sources
  stay in Messaging, while A2A, cron, kanban, webhook/API, subagent, tool, and
  unknown future system runners remain outside Projects/Home. This is a query
  policy only; it never mutates session rows and source-specific/search history
  remains available.
- Entering a project hides global recents and shows only that project's hydrated
  repo/worktree/session lanes. Switching back to flat Sessions mode preserves
  the upstream flat-list behavior.
- Project-lane paging uses a visible `Show N more in <lane>` label rather than
  an ambiguous ellipsis.
- Home is a first-class creation and reassignment target: selecting it clears
  the durable active project pointer, visibly marks Home active, and exposes the
  same hover `+` affordance as persisted projects. Moving an existing session
  to Home re-anchors it at the backend user-home directory and clears persisted
  Git metadata, preserving a valid tool cwd without inventing a second
  assignment database.
- Global Recent/search rows show textual Project/Home ownership, and the focused
  chat's statusbar always names its Project or explicit Home. Session reassignment
  shows the current owner checked instead of hiding it.
- Create Project accepts a typed directory. Only explicit Create submission may
  create a missing directory. Local mode routes through narrow Electron IPC;
  remote mode routes through the active profile's authenticated
  `POST /api/fs/ensure-directory`. Typing and directory browsing never mutate
  disk, and failures remain in the dialog with a visible error.
- In **All Profiles**, the local `/api/profiles/projects/tree` aggregate is only
  authoritative for profiles whose databases live on that gateway. Local
  handles pinned to remote gateways are queried through their own background
  profile sockets and merged into the same tree without changing the foreground
  profile; their session rows remain tagged with the local handle used by the
  profile rail.

Protected files: `apps/desktop/src/app/chat/sidebar/{index,sessions-section,project-dialog,
session-actions-menu,session-row}.tsx`, `apps/desktop/src/app/chat/sidebar/projects/
{overview-row,workspace-header}.tsx`,
`apps/desktop/src/{lib/desktop-fs.ts,lib/session-source.ts,store/projects.ts}`,
`apps/desktop/src/app/{session/hooks/use-session-list-actions,shell/hooks/use-statusbar-items}.tsx`, the matching
Desktop tests/locales/bridge declarations, `hermes_cli/{session_source_policy,
web_models,web_server}.py`, `tui_gateway/{server,methods_config,methods_session,
project_tree}.py`, and the focused backend
tests.

Focused verification:

```bash
cd apps/desktop
NODE_ENV=test ../../node_modules/.bin/vitest run --project ui \
  src/app/chat/sidebar/project-dialog.test.tsx \
  src/app/chat/sidebar/sessions-section.test.tsx \
  src/app/chat/sidebar/session-actions-menu.test.tsx \
  src/app/chat/sidebar/projects/overview-row.test.tsx \
  src/app/chat/sidebar/projects/workspace-header.test.tsx \
  src/app/shell/hooks/use-statusbar-items.test.tsx \
  src/app/session/hooks/use-session-list-actions.test.tsx \
  src/lib/desktop-fs.test.ts src/lib/session-source.test.ts \
  src/store/projects.test.ts
npm run typecheck

cd ../..
scripts/run_tests.sh \
  tests/hermes_cli/test_web_server_fs.py \
  tests/tui_gateway/test_project_tree.py \
  tests/tui_gateway/test_project_tree_source_policy.py \
  tests/tui_gateway/test_projects_rpc.py -q
```

Drop condition: upstream must provide the complete user-visible behavior set together:
a hybrid Projects + flat-recents overview with bounded/active-safe disclosure
previews and separate drill-in, complete membership independent of rendering
limits, no global recents during drill-in, labeled project-lane paging, explicit
Project/Home identity and reassignment, a Home creation target that clears
active-project state, and explicit-submit-only typed-path
creation across both local Electron and authenticated/profile-routed remote
Desktop, plus source-filter parity that keeps messaging and automation/system
runs out of Projects/Home without database mutation or title heuristics. Partial
Projects UI parity is not sufficient to drop the remote mutation or source
policy seams, and a remote mkdir endpoint alone is not sufficient to drop the
overview carry. All Profiles must also discover projects for local profile
handles pinned to remote gateways through profile-owned background connections,
without switching the foreground profile or collapsing the local handle identity.

## Axiom Desktop Project/worktree session lifecycle

Project overview and drill-in expose separate actions for a session in the main
Project checkout and a session in a newly created isolated worktree. The latter
must reuse the existing Desktop worktree dialog, Git facade, Project store, and
`/worktree` backend semantics; do not introduce a parallel worktree manager.
The focused coding row also offers **Move current session to new worktree**, which
preserves the current conversation while retargeting its cwd, alongside explicit
**Return to Project checkout** and existing-worktree entry. Project-level actions
stay disabled when the folder is not a Git repository, and failures remain visible.

Protected files: `apps/desktop/src/app/chat/sidebar/projects/{project-worktree-actions,
entered-content,overview-row,workspace-header,worktree-dialog}.tsx`,
`apps/desktop/src/app/chat/composer/status-stack/coding-row.tsx`,
`apps/desktop/src/store/{coding-status,projects}.ts`, matching tests/locales, and
the existing Electron Git bridge declarations they call.

Drop condition: upstream provides equivalent separate new-session/current-session
worktree flows, project-checkout return, existing-worktree entry, non-Git gating,
and visible failures through the existing Git/worktree infrastructure.

## Fork footprint reduction — extracted modules

To honor rule #4 ("port Axiom behavior into upstream's new split/module rather
than re-expanding the old god file"), fork-only code that previously lived
inline in upstream's most-refactored files is being moved into **fork-owned
modules** with a thin seam back into the host file. Upstream never edits a
filename it doesn't ship, so a fork-owned module has ~zero merge surface.

### `hermes_cli/axiom_update.py` — deploy-branch update helpers (2026-06-21)

**Why:** `hermes_cli/main.py` is upstream's #1 conflict hotspot — it is under an
active "god-file Phase 2" campaign (commits like *"extract 25 more subcommand
parsers into hermes_cli/subcommands/"*, *"extract 18 model-flow wizard
functions"*). The fork carried **+1623/−537** lines there, so nearly every
upstream merge collided. Measured fork divergence at extraction time: 309
fork-only commits; `main.py` touched by 31 of them.

**What moved:** 18 imported seam helpers plus internal support functions (≈950 lines), all in the
deploy-branch update domain — none exist upstream, so they carry with zero
`main.py` merge surface:

```text
_run_deploy_branch_update            _sync_deploy_main_to_upstream
_validate_update_after_pull
_completed_deploy_handoff_requires_post_update
_record_deploy_handoff               _deploy_handoff_marker_path
_deploy_handoff_exists_for           _resolve_deploy_handoff
_count_changed_from_pre_update       _print_deploy_branch_handoff
_preserve_deploy_branch_stash        _remove_update_worktree
_clean_managed_worktree              _short_git_ref
_get_dashboard_service_pids          _desktop_shortcut_exists
_detect_windows_gateway_launcher_instances
```

Plus fork-only update metadata constants such as `DEPLOY_HANDOFF_FILE`, `UPDATE_REVIEW_DIR`, and `FORK_WATCH_AREAS`.

**Conflict-review / resolve carry:** deploy-branch merge conflicts automatically generate a visible operator review and full markdown report under `~/.hermes/update-reports/`. The LLM summary is best-effort/advisory only; if auxiliary LLM review fails, the updater prints and writes a deterministic brief. Bare `hermes update` runs the non-interactive Hermes resolver in the retained worktree and streams its transcript under an explicit advisory banner. The child performs structural resolution only. The parent rejects untracked files, checkpoints tracked changes, persists the exact checkpoint SHA and resumable check status, runs matched focused checks serially, pushes the validated SHA to `<deploy>`, fast-forwards the live checkout, and clears `.update_handoff.json` only after publication. Validation and push failures retain the checkpoint/worktree without mutating live source; legacy phase-less and `push_pending` markers remain readable.

**Seam contract:**
- `main.py` imports the public seam helpers from `hermes_cli.axiom_update` at module load
  (one import block, just after the `subcommands.*` imports) and calls them at
  the original call sites unchanged (8 external seam sites; the rest are
  internal cross-calls that travel together).
- `axiom_update.py` imports four still-in-`main.py` helpers
  (`_count_commits_between`, `_hermes_exe_shims`, `_is_windows`,
  `_validate_critical_files_syntax`) **lazily, inside the functions that use
  them**, to avoid a circular import at load time. These are stable upstream
  utilities — import them, do not move them, so the new module stays free of
  upstream-churning code.
- Tests that exercise a moved function must patch its dependencies on
  `hermes_cli.axiom_update` (where the function resolves them), not on
  `hermes_cli.main`. See
  `tests/hermes_cli/test_update_autostash.py::test_deploy_handoff_marker_completes_when_live_origin_and_upstream_match`.

**Still inline in `main.py` (deliberately not extracted):** the *modifications*
to upstream functions (`_cmd_update_impl`, `_cmd_update_check`,
`_print_version_info`, etc.). Those rewrite upstream code paths by design;
their merge conflicts are the intended signal that upstream touched the update
flow and the carry needs review. Do not hide them by extraction.

**Drop/review condition:** when upstream lands an equivalent deploy-branch
update mechanism, retire `axiom_update.py` per the drop-review process rather
than letting it rot.

**Validation:** `python -m py_compile hermes_cli/axiom_update.py
hermes_cli/main.py`; the update suites in the FORK.md validation block;
`hermes --version` and `hermes update --check` smoke through the live CLI.

## Protected behavior contract

### 1. Hermes-Relay / external API compatibility

These routes and semantics are part of the Axiom/Hermes-Relay compatibility surface until explicitly retired:

> **RETIRED 2026-06-16 — see "Retired fork surface" below.** The relay-specific api_server patches that provided these routes were stripped from `axiom` (commit `6924d6356`); Hermes-Relay now relies on native upstream + the plugin-owned bootstrap, never fork patches. The list below is retained as the historical compatibility surface and verification baseline, not still-protected fork code.

```text
/api/sessions
/api/sessions/{id}
/api/sessions/{id}/messages
/api/sessions/{id}/chat
/api/sessions/{id}/chat/stream
/api/sessions/{id}/fork
/api/sessions/search
/api/memory
/api/skills
/api/config
/api/available-models
/api/audio/capabilities
/api/audio/transcriptions
/api/audio/speech
/voice/config
/voice/transcribe
/voice/synthesize
```

Protected semantics:

- session-aware chat and streaming endpoints persist user messages correctly;
- SSE events include stable `session_id`, `run_id`, sequence/timestamp metadata where expected;
- `/api/sessions/{id}/chat/stream` emits live `tool.started` / `tool.completed` style events for external clients;
- image/multimodal attachments survive the API server path;
- Hermes-Relay bootstrap feature detection runs after native fork routes are registered, so bootstrap shims do not shadow first-class handlers;
- route registration must not duplicate method/path pairs;
- `/api/plugins/*` and other sensitive admin surfaces require bearer auth where fork patches added it.

Known references:

- `DEVLOG.md` 2026-06-05: duplicate API session route cleanup and Axiom/Relay-only compatibility routes.
- `DEVLOG.md` 2026-06-03: upstream sync preserving API/session behavior.
- `DEVLOG.md` 2026-04-23: TUI/plugin-command and router compatibility during upstream merge.

Primary files:

```text
gateway/platforms/api_server.py
webapi/*
tests/gateway/test_api_server.py
tests/gateway/test_session_api.py
tui_gateway/server.py
```

### 2. Forge integration

Protected behavior:

- Forge outbound delivery adapter remains registered and can deliver webhook/gateway responses back to Forge chat threads.
- `[SILENT]` output is treated as a successful no-op delivery.
- Forge streaming draft support remains available where `chat.startDraft`, `chat.appendDraftChunk`, and `chat.finalizeDraft` are expected.
- Forge per-run host tool policy is enforced before tool dispatch and inherited by delegated subagents.

Known references:

- `DEVLOG.md` 2026-06-06: Forge per-run host tool policy.
- `DEVLOG.md` 2026-05-18: Forge platform delivery adapter.

Primary files:

```text
plugins/platforms/forge/*
agent/runtime_tool_policy.py
model_tools.py
tests/gateway/test_forge_plugin.py
tests/gateway/test_api_server_runs.py
tests/gateway/test_api_server_toolset.py
tests/agent/test_runtime_tool_policy.py
tests/test_model_tools.py
tests/tools/test_delegate.py
```

### 3. Webhook route-level toolsets

Protected behavior:

- Generic/public webhook routes remain constrained by default.
- Trusted webhook routes can specify route-level `toolsets` and pass them to generated `MessageEvent` / agent creation.
- CLI and docs support `hermes webhook subscribe --toolsets ...`.

Known references:

- `DEVLOG.md` 2026-05-28: route-level webhook toolsets.

Primary files:

```text
gateway/platforms/webhook.py
gateway/run.py
hermes_cli/webhook.py
tests/gateway/test_webhook_adapter.py
tests/hermes_cli/test_webhook_cli.py
tests/gateway/test_reasoning_command.py
```

### 5. Proxy / provider routing

Protected behavior:

- Routed OAuth proxy adapters remain available for authenticated subscriptions where configured.
- Anthropic OAuth must preserve the Claude Code subscription billing lane: OAuth tool names use `mcp__*` on the wire, concrete `tool_choice` names are encoded the same way, and the large Hermes system prompt is relocated out of Anthropic `system[]` for OAuth requests.
- OpenAI Codex, xAI OAuth, and Nous routed model discovery remains compatible with Axiom's local model-router/proxy expectations.
- Codex chat completion translation remains intact.
- MoA provider slots for OAuth/adapter-backed providers (`anthropic`, `openai-codex`, `xai-oauth`) preserve the named provider identity instead of passing resolved `base_url`/`api_key` through `call_llm`, which would downgrade the slot to `custom` and bypass the provider-owned auth/request-shape path.
- Synthetic/fallback model inventory must not advertise stale or non-chat model IDs to downstream routers.
- Local provider/model config values may be dict-shaped and must not crash model resolution.

Known references:

- `DEVLOG.md` 2026-05-19: routed proxy model inventory and `gpt-5.5` advertisement.
- `DEVLOG.md` 2026-04-23: Anthropic OAuth shim cleanup while preserving Model Router/plugin-command seams.
- `DEVLOG.md` 2026-06-17: Claude OAuth billing-lane candidate stack (#47723 + #23361 + #47738) and live `claudetest` smoke.
- `cc430e8c3 fix(moa): preserve anthropic slot identity` and `109fce4ee fix(moa): sync virtual provider runtime handling`.

Primary files:

```text
hermes_cli/proxy/*
agent/anthropic_adapter.py
gateway/builtin_hooks/boot_md.py
hermes_cli/runtime_provider.py
agent/moa_loop.py
agent/auxiliary_client.py
tests/hermes_cli/test_proxy.py
tests/agent/test_anthropic_adapter.py
tests/run_agent/test_moa_loop_mode.py
tests/agent/test_auxiliary_main_first.py
```

### 6. Update / deploy branch behavior

Protected behavior:

- Axiom deploy branch strategy remains explicit: upstream is reconciled into `origin/axiom` in a temporary worktree before the live checkout fast-forwards.
- `hermes update` / update checks understand fork deploy branches and do not incorrectly declare up-to-date by checking only `origin`.
- Deploy branch updates are transactional and preserve rescue prompts for stash/merge conflicts.
- Deploy-branch merge conflicts automatically print an `Update conflict review`, write a full markdown report under `~/.hermes/update-reports/`, and use LLM review only as a best-effort advisory layer with deterministic fallback. Bare `hermes update` streams the resolver agent's advisory transcript from the retained worktree, then independently validates focused checks, pushes, fast-forwards, and finishes install/restart; safety failures leave live source untouched.
- The first host to observe upstream work publishes one reconciled `origin/<deploy>` artifact. Later hosts fast-forward to it rather than repeating the same resolution.
- Update path can restart named profile gateway services without leaking profile env between processes.
- Update path excludes systemd-managed dashboard/Desktop child processes from unsafe kill sweeps.
- Windows update path pauses mapped gateway processes before the concurrent `hermes.exe` shim guard, so Scheduled-Task/manual gateways can release `venv\\Scripts\\hermes.exe` before dependency reinstall; unrelated REPL/Desktop backend processes still block unless `--force` is explicit.
- Windows quarantine safeguards remain intact.
- Pipeline TUI / update handoff context remains agent-readable.

Known references:

- `DEVLOG.md` 2026-06-03: upstream sync and service refresh.
- `DEVLOG.md` 2026-06-18: Windows gateway pause before concurrent `hermes.exe` update guard.
- fork-only commits tagged `fix(update)`, `feat(update)`, `fix(banner)`, `fix(version)`, `fix(cli)`.

Primary files:

```text
hermes_cli/main.py
hermes_cli/axiom_update.py
hermes_cli/config.py
hermes_cli/banner.py
hermes_cli/update_ui.py
hermes_cli/gateway.py
hermes_cli/web_server.py
scripts/check-merge-drops.py
tests/hermes_cli/test_update_check.py
tests/hermes_cli/test_update_autostash.py
tests/hermes_cli/test_update_stale_dashboard.py
tests/hermes_cli/test_update_concurrent_quarantine.py
tests/hermes_cli/test_cmd_update.py
```

### 7. TUI / plugin-command cards / image attach

Protected behavior:

- Plugin command metadata/card support remains available for CLI/gateway/TUI paths.
- TUI `command.dispatch` handles plugin commands with full entries, `session_id`, and card rendering.
- Gateway-only plugin commands route correctly through command dispatch.
- `image.attach.bytes` RPC remains available for remote clients if still used by Hermes-Relay/Desktop flows.

Known references:

- `DEVLOG.md` 2026-04-23: TUI plugin-command alignment.
- fork-only commit `feat(tui_gateway): add image.attach.bytes RPC for remote clients`.

Primary files:

```text
hermes_cli/plugins.py
hermes_cli/commands.py
gateway/cards.py
tui_gateway/server.py
tests/hermes_cli/test_plugins.py
tests/tui_gateway/test_protocol.py
```

### 8. Local memory / Lucid / neural-memory operations

Protected until reviewed:

- MemPalace retired state is not reintroduced accidentally.
- Lucid/neural-memory update hooks and dashboard paths remain aligned with current Axiom docs and services.
- Context compression / memory behavior that has Axiom-specific patches is tested before merge.

Primary files:

```text
plugins/memory/neural/*
agent/context_compressor.py
tests/agent/test_context_compressor.py
tests/plugins/memory/test_mempalace_provider.py
```

### 9. MCP OAuth stream concurrency

Protected behavior:

- Cached OAuth credentials and refresh-token exchanges remain serialized, but the OAuth provider does not hold the SDK's task-owned lock across long-lived MCP Streamable-HTTP/SSE application responses.
- Concurrent authenticated RPCs such as `initialize` followed by `tools/list` must not deadlock when the first response stream remains open.
- First-time authorization and HTTP 401 recovery continue to use the SDK's complete OAuth discovery flow.

Primary files:

```text
tools/mcp_oauth_manager.py
tests/tools/test_mcp_oauth_bidirectional.py
```

Verification:

- `pytest -q tests/tools/test_mcp_oauth_bidirectional.py tests/tools/test_mcp_oauth_manager.py -o 'addopts='`
- Live OAuth MCP smoke against TREK: initialize, discover tools, and call read-only `list_trips`.

Drop condition: remove the carry when the MCP Python SDK releases its OAuth context lock before yielding Streamable-HTTP application requests, or Hermes adopts an equivalent upstream workaround with concurrent-stream regression coverage.

## Temporary upstream PR carries

Carried commits from open upstream PRs are merged into `axiom` with `--no-ff` from a dedicated `carry/upstream-pr-<number>-<topic>` branch so the carry can be reverted as a unit when upstream merges or replaces the feature.

### PR #75049 — Buzz active-thread mention policy

- **Status:** AXIOM CARRY — contributor-authored runtime/config/test/docs subset carried from open upstream PR #75049 on 2026-07-31. The unrelated Dashboard Buzz icon and generic `AutoField` accessibility changes from the PR are intentionally not carried.
- **PR:** https://github.com/NousResearch/hermes-agent/pull/75049
- **Carry branch/worktree:** `carry/buzz-active-thread-mentions-75049` at `~/.hermes/worktrees/hermes-buzz-75049/`.
- **Why carry:** Current upstream applies `require_mention` independently to every inbound Buzz channel event. The agent can reply in a thread, but every human follow-up still needs another mention. Waiting for upstream would leave a reproduced live messaging defect in canonical Docker-Server Victor.
- **Required behavior:** `require_mention: true` continues gating unrelated channel conversations. With `thread_require_mention: false`, an initial mention invites Hermes into a thread and later authorized replies in that successfully-participated thread dispatch without another mention. DMs and channel/user allow-lists remain unchanged. The backward-compatible default remains `thread_require_mention: true`; Victor opts out explicitly in profile-local config.
- **Files carried:** `cli-config.yaml.example`, `hermes_cli/config_defaults.py`, `plugins/platforms/buzz/adapter.py`, `tests/gateway/test_buzz_adapter.py`, `website/docs/reference/environment-variables.md`, and `website/docs/user-guide/messaging/buzz.md`.
- **Verification:** `/home/bailey/.hermes/hermes-agent/.venv/bin/python -m pytest -q tests/gateway/test_buzz_adapter.py tests/gateway/test_buzz_websocket.py --tb=short` → `41 passed`; touched-file Ruff and `git diff --check` passed.
- **Separate upstream work:** PR #74516 adds Buzz reply-thread session identity through `MessageSource.thread_id`. It is deliberately not stacked here because it overlaps the same adapter heavily and is not required to fix mention admission. Re-evaluate after upstream consolidates or merges the competing Buzz thread-session PRs.
- **Merge/rebase rule:** Preserve this carry while #75049 is open. If upstream changes the Buzz adapter, retain successful-send participation tracking, nested root resolution, restart reconstruction, bounded out-of-order follow-up handling, allow-list precedence, and the explicit strict-thread default. Do not resolve a conflict by setting `require_mention: false` globally.
- **Drop condition:** Once upstream merges #75049 or equivalent behavior, remove this carry as a unit, retain Victor's explicit `thread_require_mention: false`, rerun the focused Buzz suite, and complete a hosted-relay smoke test before restarting the gateway.

### RETIRED 2026-08-02 — PR #41711 A2A carry

- **Upstream replacement:** https://github.com/NousResearch/hermes-agent/pull/77109 merged to `upstream/main` and closed issue https://github.com/NousResearch/hermes-agent/issues/514. PR #41711 was closed as a stale-base predecessor after Teknium salvaged its commits and the community follow-up stack with authorship preserved.
- **Retirement:** Axiom now takes upstream's A2A v1 implementation wholesale: canonical Agent Card discovery, legacy compatibility, final-reply gating, reconnect compatibility, routable card URLs, bearer-token authorization, tenant isolation, SSE streaming, file/data Parts, push CRUD, history, and orchestration. The old plugin/test carry and Axiom notice-prefix workaround are no longer protected fork surface.
- **Retained generic seam:** Axiom commit `9c06b9874` remains active in `hermes_cli/plugins.py`. Explicitly enabled bundled platform plugins must load eagerly when they also register tools; otherwise A2A's outbound tools stay deferred and do not appear in `hermes tools`. This is generic plugin behavior, not an A2A implementation fork.
- **Historical deployment:** Docker-Server Victor (`100.71.8.56:9900`), TGI Docker (`100.84.156.70:9900`), and Axiom-Desktop (`100.105.160.1:9900`) were deployed from the carry with host-local bearer credentials. Preserve host-local secrets and rerun reciprocal live smoke tests after each host consumes the upstream v1 implementation.
- **Cleanup:** retire the #41711 watcher after this merge is published; update the separate `tgi` deploy branch from upstream rather than retaining its old `94b54a08b` overlay as protected code.


### Windows HUD stability — local carry pending upstream equivalent

Axiom carries a compact Windows HUD stabilization layer because the upstream
transparent frameless window can grow during drag and remain native
click-through after the pointer returns to the composer.

Protected behavior:

- HUD uses a non-resizable native `BrowserWindow` on Windows; drag movement
  pins the size captured at window creation.
- Windows persisted geometry beyond twice the `620x320` default envelope is
  treated as Windows drag-growth corruption and ignored on the next HUD open;
- Windows uses the same main-process cursor-position feed as Linux so the
  renderer can clear `WS_EX_TRANSPARENT` even when Electron forwarding stalls.
- Programmatic close paths suppress only the HUD restore/broadcast behavior;
  cleanup listeners remain attached so cursor polling cannot leak timers.

Primary files:

- `apps/desktop/electron/hud-window-geometry.ts`
- `apps/desktop/electron/hud-window-geometry.test.ts`
- `apps/desktop/electron/hud-cursor.ts`
- `apps/desktop/electron/hud-cursor.test.ts`
- `apps/desktop/electron/hud-window-lifecycle.ts`
- `apps/desktop/electron/hud-window-lifecycle.test.ts`
- `apps/desktop/electron/main.ts`

Retirement condition: upstream must provide equivalent or better packaged
Windows behavior for geometry stability **and** click-through recovery. An
upstream resize handle without the Windows cursor recovery is not sufficient.
Verify with physical drag/composer/click/exit smoke tests before dropping the
carry. Operator details and focused commands live in
`docs/axiom-fork-contract.md`.

### Desktop remote profile handles — local carry pending upstream equivalent

Axiom carries `feat(desktop): add remote profile handles` so Desktop can discover named profiles exposed by a selected remote gateway and pin them as local profile handles. This turns the existing profile rail into the practical local/remote agent switcher without hand-creating stub profiles or copying remote connection settings.

Primary files:

- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/electron/remote-profile-auth.ts`
- `apps/desktop/electron/remote-profile-auth.test.ts`
- `apps/desktop/src/app/settings/gateway-settings.tsx`
- `apps/desktop/src/global.d.ts`
- `apps/desktop/src/i18n/*.ts`
- `docs/refs/2026-06-desktop-remote-profile-handles.md`

Why Axiom needs it:

- Upstream Desktop can save per-profile remote gateway overrides, but operators still have to create local stubs and copy connection settings by hand before remote profiles are visible in the profile rail.
- Axiom/TGI need a low-friction way to load named profiles from a remote gateway and pin them as local Desktop profile handles so the existing profile rail/sidebar becomes the local/remote agent switcher.
- The local handle must be independent from the remote profile name: for example, a remote `default` / Atlas can be pinned locally as `tgi-atlas` without colliding with the local `default` / Atlas.
- The closest upstream design, draft PR #39337, is broad and stale; this patch intentionally ports only the narrow discover-and-pin workflow.

Required behavior:

- Settings -> Gateway Connection shows a Remote profiles panel when the selected scope is remote.
- The panel can call the selected remote gateway's `/api/profiles` endpoint using the saved token/OAuth connection path. Cookieless native PKCE sessions use the encrypted bearer token; legacy OAuth sessions retain the cookie fallback.
- A remote profile can be added as a distinct local profile handle, then pinned to that remote gateway as a per-profile remote override while preserving `remoteProfile` metadata through sanitized config, REST routing, and WebSocket URL generation.
- Selecting that handle from the existing profile rail routes future Desktop traffic to the target remote profile; switching back to a local profile uses the local backend.

Retirement criteria:

- Upstream provides an equivalent or better Desktop workflow for discovering remote profiles/gateways, showing them beside local profiles, and switching without manual stub creation or token copying.
- Upstream routes chat/session/profile-scoped settings to the selected backend correctly and handles dead remotes visibly.
- Once upstream covers those outcomes, remove this IPC/UI/string patch and keep the upstream implementation.

Watch upstream for:

- PR #39337 or successor peer-gateway/profile selector work.
- Desktop changes mentioning peer gateways, remote profile discovery, connection registry, gateway selector, per-profile routing, profile switch races, or model/session refresh on profile swap.

Reference:

- `docs/refs/2026-06-desktop-remote-profile-handles.md`

Focused checks:

```bash
cd apps/desktop
npm run typecheck
NODE_ENV=test npx vitest run --project electron electron/connection-config.test.ts electron/remote-profile-auth.test.ts
NODE_ENV=test npx vitest run --environment jsdom src/app/settings/gateway-settings.remote-profiles.test.ts
```

### Desktop profile visibility, profile-scoped tabs, and workspace appearance — local carry pending upstream equivalent

Axiom lets operators hide named profiles from Desktop browsing without deleting,
disabling, or disconnecting them. The preference is Desktop-local presentation
state: Settings -> Appearance can restore every hidden profile, while Manage
Profiles and Gateway Settings continue exposing the complete profile inventory.
The default profile cannot be hidden.

Protected behavior:

- hidden profiles are excluded consistently from the profile rail, condensed
  picker, rail keyboard slots/cycling, All Profiles browsing/groups, and the
  sidebar profile filter;
- profile data, sessions, direct links, explicit search results, ownership tags,
  connection settings, and Manage Profiles remain intact and addressable;
- right-clicking a named profile rail icon offers **New chat**, which creates an
  unlisted session tab in the current window and sends the clicked profile
  explicitly through `session.create`, regardless of the currently foregrounded
  profile or All Profiles browse state;
- the same context menu exposes a separate **New window** action. Its optional
  profile travels through the typed preload/IPC bridge and a pre-hash renderer
  query so gateway boot adopts the clicked profile before connecting; generic
  File/keyboard New Window remains unqualified;
- switching profiles swaps the complete profile-owned workspace rather than
  reusing the previous profile's tab strip and pane tree: session tabs, route/page
  tiles, file/browser/artifact previews, active preview, tree geometry/preset,
  pane open/resize state, dismissed/user-placed panes, split-share memory, rail
  orientation, terminal/review visibility, selected review file, and per-zone
  composer pop-out geometry all round-trip independently;
- a fresh workspace draft still offers **Close** in the tab context menu when
  sibling session tabs exist: the registered semantic closer promotes the next
  session while the structural `workspace` pane remains unremovable;
- profile switches only remount the visible pane membership. Session tile/runtime
  stores retain each profile's backend state, so hiding another profile's tabs
  never cancels its active turn;
- pre-profile flat storage migrates into the `default` workspace and remains its
  rollback-compatible mirror. Named profiles begin from declared pane/layout
  defaults and persist in profile maps; secondary chat windows still never write
  the primary window's workspace;
- profile bundle export/import reads or writes the named profile's layout without
  switching or overwriting the currently visible profile;
- Appearance scope stays intentionally mixed: skin/mode, terminal font, pet,
  backend theme, and workspace presentation follow the profile; UI scale,
  translucency/backdrop, language, accessibility/motion, embed consent, tool-card
  verbosity, installed-theme catalog, keybinds, and sidebar sort/grouping remain
  operator/app-global preferences.

Primary files: `apps/desktop/src/store/profile.ts`,
`apps/desktop/src/app/chat/sidebar/{profile-switcher,index,filter-menu}.tsx`,
`apps/desktop/src/app/session/hooks/use-session-actions/index.ts`,
`apps/desktop/src/lib/profile-persisted.ts`,
`apps/desktop/src/components/pane-shell/tree/store.ts`,
`apps/desktop/src/store/{panes,route-tiles,preview,layout,review,
composer-popout,profile-share}.ts`,
`apps/desktop/src/app/{chat/pane-mirror,right-sidebar/store}.ts`,
`apps/desktop/src/store/windows.ts`, `apps/desktop/electron/{main,preload,
session-windows}.ts`, and
`apps/desktop/src/app/settings/profile-visibility-settings.tsx`.

Drop condition: upstream provides equivalent persisted profile visibility controls
and profile-icon context actions whose current-window tab and peer window are
explicitly bound to the clicked profile, plus equivalent profile-owned tab,
layout, pane, preview, and workspace-presentation persistence with safe legacy
migration, non-active profile import/export, and hidden profiles still
recoverable with backend work/data untouched.

Focused checks:

```bash
cd apps/desktop
npm run typecheck
NODE_ENV=test npm run test:ui -- \
  src/lib/profile-persisted.test.ts \
  src/store/profile-workspace-persistence.test.ts \
  src/store/profile-share.test.ts \
  src/themes/profile-theme.test.ts \
  src/store/composer-popout.test.ts \
  src/store/preview.test.ts \
  src/store/panes.test.ts \
  src/store/session-states.test.ts
```

### Desktop project overview, default project, and in-chat live status — local carry pending upstream equivalent

Axiom keeps Project-mode navigation compact without conflating the project tree
with its Recent Sessions lane, and makes new-session workspace selection explicit.
It also keeps live activity visible inside the selected conversation instead of
relying on the sidebar highlight or pane-tab dot alone.

Protected behavior:

- the existing Projects chevron collapses only the project overview rows; Recent
  Sessions remains mounted and visible, and the disclosure state persists locally;
- Settings -> Sessions can choose a saved project as the default for new sessions,
  displaying both its name and resolved root; the saved root is qualified by
  connection and profile so remote paths never pass through the local Electron
  filesystem setter, while the existing manual-folder picker remains available;
- explicit project scope still wins over the configured default, and selecting a
  named project replaces rather than stacks with a stale manual-folder default;
- every busy selected/tiled chat surface renders **Working…** in the centered
  control lane of its composer, compacting to the animated indicator in narrow
  or popped-out composers; it transitions to **Waiting for your input** only
  when the session-scoped prompt store reports a blocking clarify, approval,
  sudo, or secret request, and clears when the session settles;
- sidebar live-status keys remain profile-qualified even when a stream-seeded
  runtime publishes before its profile metadata arrives: a missing state profile
  may adopt the unique owning profile from the authoritative stored-session row,
  but duplicate IDs across profiles never guess or leak status between profiles.

Primary files: `apps/desktop/src/store/{projects,session,session-states,
session-dot-state}.ts`,
`apps/desktop/src/app/chat/{index,live-session-status}.tsx`,
`apps/desktop/src/app/chat/composer/index.tsx`,
`apps/desktop/src/app/chat/sidebar/{index,sessions-section}.tsx`, and
`apps/desktop/src/app/settings/default-project-setting.tsx`.

Drop condition: upstream provides equivalent project-only disclosure, a
connection/profile-safe saved-project default that governs new-session cwd, and
a persistent session-scoped live indicator in the main chat surface, including
profile-safe sidebar status while live metadata is still converging.

### Desktop native OAuth orchestration — local carry pending upstream equivalent

Axiom keeps native RFC 8252 sign-in single-flight per normalized gateway in Electron main. Multiple renderer surfaces share one pending login instead of opening competing loopback listeners and overwriting browser PKCE state. A gateway that advertises `native_pkce` never silently changes to the embedded cookie flow after timeout, cancellation, or token-exchange failure; the error surfaces and an explicit Retry starts a fresh exchange. Embedded login remains the compatibility path only for gateways that do not advertise native PKCE.

Primary files: `apps/desktop/electron/main.ts`, `apps/desktop/electron/oauth-login-coordinator.ts`, and `apps/desktop/electron/oauth-login-coordinator.test.ts`.

Drop condition: upstream owns native login as a process-wide single-flight operation and keeps fallback capability-gated rather than error-triggered.

### Desktop OAuth remote artifact opening — local carry pending upstream equivalent

Axiom carries `fix(desktop): open OAuth remote artifacts from gateway session` so Desktop can preview/open gateway-local artifacts from a remote backend authenticated through dashboard/basic OAuth, not only the legacy token-mode URL path.

Primary files:

- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/src/global.d.ts`
- `apps/desktop/src/app/artifacts/index.tsx`
- `apps/desktop/src/lib/desktop-fs.ts`
- `apps/desktop/src/lib/desktop-fs.test.ts`
- `apps/desktop/src/lib/media.ts`
- `apps/desktop/src/lib/media.remote.test.ts`
- `apps/desktop/src/app/artifacts/index.test.ts`

Required behavior:

- Remote artifact image cards fetch gateway-local images through the authenticated REST bridge and include the owning profile when present.
- Transcript `MEDIA:` / `#media:` image previews fetch gateway-local images through the active remote profile instead of falling back to the primary/default backend.
- Remote filesystem preview/read-data-url calls include the active remote profile so image/file open-download paths follow the same backend as the live chat.
- Opening a gateway-local artifact in remote OAuth mode asks Electron main to download it through the OAuth session partition, write a local temp copy, and open/reveal it via the OS.
- Token-mode remote artifacts keep using the existing token-authenticated path.
- Browser-native `http(s)` and `data:` artifacts remain normal external links/previews.

Upstream watch / retirement criteria:

- Watch upstream Desktop/API work touching remote media/artifacts/profile-scoped filesystem reads, especially commits/PRs mentioning `remote media`, `remote-gateway artifacts`, `OAuth`, `dashboard auth`, `MEDIA:`, `/api/media`, `/api/fs/read-data-url`, `openRemoteFile`, or profile-scoped Desktop REST calls.
- Do **not** drop this carry just because upstream has token-mode remote media support. Upstream must cover the Axiom default path: Desktop shell connected to a remote gateway using OAuth/dashboard-session auth, with artifacts/files living on the gateway host.
- Equivalent upstream behavior must satisfy every required behavior above, including transcript `MEDIA:` / `#media:` image previews, Artifacts-page previews, file open/download, active-profile routing, explicit artifact-owned profile routing, token-mode compatibility, and ordinary `http(s)` / `data:` passthrough.
- Before retiring, compare upstream against these files and run the focused checks below. Remove this section only after upstream passes the checks without the fork-only hunks.

Focused checks:

```bash
cd apps/desktop
npm run typecheck
NODE_ENV=test npx vitest run --environment jsdom \
  src/lib/media.remote.test.ts \
  src/lib/media.test.ts \
  src/lib/desktop-fs.test.ts \
  src/app/artifacts/index.test.ts
```

### Desktop unified multi-profile session identity

The optional **All profiles** sidebar must treat session identity as `(profile, session id)`, not as a globally unique bare id. Profile clones can legitimately contain the same stored session ids.

Required behavior:

- Regular and virtualized sidebar rows preserve `session.profile` through open/resume actions and use profile-qualified React keys.
- Branch flattening/deduplication remains profile-scoped, so same-id rows from different profiles both remain visible and branches never attach across profiles.
- Only the active profile's copy of a same-id session is selected.
- Cross-profile sidebar navigation carries a one-shot profile intent through the route hop, resolves the exact profile row/backend, bypasses an unqualified warm-runtime cache hit, and performs a real resume on the target gateway.
- Resolving/upserting one profile's session must not remove a same-id row owned by another profile.

Focused checks:

```bash
cd apps/desktop
npm run typecheck
NODE_ENV=test npx vitest run --environment jsdom \
  src/lib/session-branch-tree.test.ts \
  src/app/chat/sidebar/sessions-section.test.tsx \
  src/app/session/hooks/use-session-actions/resolve-stored-session.test.ts \
  src/app/session/hooks/use-route-resume.test.tsx \
  src/store/profile.test.ts
```

## Current known update/build pitfalls

### Desktop model picker snap-back — retired 2026-07-31

- **Status:** RETIRED IN FAVOR OF UPSTREAM. The active `use-model-controls.ts` implementation is byte-identical to `upstream/main`; the two picker components had only blank-line drift, which was removed.
- **Original local subject:** `fix(desktop): keep model picker selection authoritative`.
- **Retained coverage:** Axiom keeps the extra active-session regression in `use-model-controls.test.tsx` because it asserts a behavioral invariant without carrying a divergent implementation.
- **Do not reintroduce:** stale `model.options` query metadata must not override the live composer/session stores after an explicit picker selection.

### PR #40946 — async background delegation

- **Status:** SUPERCEDED — upstream merged equivalent implementation on 2026-06-15.
- **PR:** https://github.com/NousResearch/hermes-agent/pull/40946
- **Upstream merge commit:** `c66ecf0bc` on `upstream/main`
- **Local carry reverted:** `1e16a11b7` reverts `500dc0fbd`
- **Files touched upstream:** `cli.py`, `gateway/run.py`, `hermes_cli/cli_commands_mixin.py`, `hermes_cli/config.py`, `tools/async_delegation.py`, `tools/delegate_tool.py`, `tools/process_registry.py`, `tui_gateway/server.py`, `tests/tools/test_async_delegation.py`
- **Verification:** Upstream squash `c66ecf0bc` is file-diff-equivalent to the carried PR commits (1268 insertions upstream vs 1266 local; 2-line delta is trailing whitespace cleanup). No Axiom contract conflicts.
- **Action:** Next upstream sync will naturally absorb upstream's version. No special handling needed.

## Retired fork surface

Discord multi-agent orchestration code and documentation were intentionally removed on 2026-06-08 at operator direction. Do not reintroduce the removed plugin, slash commands, env vars, or docs during upstream sync unless explicitly requested. Keep the generic Discord bot-admission safety controls (`allow_bots`, `thread_require_mention`, safe allowed mentions, reply-ping suppression) because they are still useful outside that retired feature.

Desktop remote-filesystem workaround patches were reverted on 2026-06-12 after upstream shipped native remote filesystem browsing (read-only) in commits around `969aeb279`. The reverted patches are:
- `e830ac3e6` — "skip remote session cwd in Files panel to prevent ENOENT" (superseded)
- `b6d71f248` — "fix desktop remote cwd workspace drift" (superseded)
- `8e5b55378` — "docs: clarify local files pane in remote mode" (superseded)

Kept without change:
- `992cfbdfd` — "widen file browser max width" (harmless UI preference)
- `7c3f8d2d0` — "narrow hover-reveal trigger strip" (scrollbar UX fix)

### Relay-specific api_server patches — retired 2026-06-16

Per operator direction, Hermes-Relay must rely on native upstream OR the hermes-relay plugin, never fork patches. `gateway/platforms/api_server.py` was reset to `upstream/main` (keeping only the non-relay Forge run-tool-policy hunk `d95b9381e`) and the retired `webapi/*` Workspace bridge was deleted. Strip commit on `axiom`: `6924d6356` (api_server.py diff vs `upstream/main` = +68 lines, Forge-only). The `api-relay` commits below are superseded:

- Session/skills/config adders (`f3121f6eb`, `8b4882542`, `9d16e123b`, `8208ea113`, `051b8e96f`, `b78288ed0`, `9ad4dc91f`, `d4e642904`): native upstream now serves `/api/sessions/*`, `/api/sessions/{id}/chat/stream`, `/v1/skills`, `/v1/toolsets`, `/v1/capabilities`.
- SSE-fallback enrichments (`d58b797bd` live tool events, `790df544b` session_id/run_id, `f3f382e5f` keepalive, `832014466` multimodal attach): the gateway `/api/ws` (tui_gateway) transport carries live tool + thinking + multimodal natively; the api_server SSE path is only a fallback.
- `73babf72a` Relay-style api_server `/api/audio/*`: standard voice rides the native dashboard `/api/audio/transcribe` + `/api/audio/speak` (:9119) instead.
- `aea9f5f48` (cron `self.` fix): folded away with the reset; upstream cron code is already correct.

Kept (not relay-specific): `629e1ec70` (bearer auth on the native dashboard `/api/plugins/*` mount — general security hardening) and `73fa63742`'s `run_agent.py` multimodal hunk (core multimodal). The remaining non-native compat routes (`/api/memory`, `/api/config`, `/api/available-models`, legacy `/api/skills` detail, `/api/sessions/search`) now come from the plugin-owned bootstrap (`hermes_relay_bootstrap.pth`, feature-detected, native wins), not fork patches.

**Verification (live deploy, 2026-06-16):** api_server.py vs `upstream/main` = +68 (Forge-only), `grep api/audio` = 0, `webapi/` removed. Gateway `/health` 200; `/v1/capabilities` `audio_api:false`; native `GET /api/sessions` 200, `/v1/skills` 200, `/v1/toolsets` 200; gateway `POST /api/audio/transcribe` 404; dashboard `POST /api/audio/transcribe` (:9119) native (`hermes_cli/web_server.py` L2411 / L2546). Relay reinstalled (`dev` 1.1.0) healthy against the stripped gateway. Pre-strip rollback commit `c25408081`.

## Fork-only commit inventory

`git cherry -v upstream/main origin/axiom` reported all 95 non-merge fork commits as `+` (not patch-equivalent to upstream). These must be classified before merge hardening is considered complete.

Category counts from commit-message classification:

```text
update-deploy:       26
misc/unknown:        20
proxy-provider:      16
api-relay:           15
docs:                 3
forge:                3
tui-relay:            2
tests/tooling:        1
webhook:              1
```

### api-relay

```text
f3121f6eb feat(api-server): add sessions, memory, skills, config endpoints
8b4882542 feat(api-server): add session-aware chat and streaming endpoints
790df544b fix(api-server): add session_id and run_id to all SSE events
9ad4dc91f fix(api-server): fast-path probe + persist user messages before agent run
8208ea113 fix(api-server): enable session persistence for session chat handlers
9d16e123b fix(api-server): auto-create sessions on first access
051b8e96f fix(api-server): remove duplicate user message persistence
832014466 feat(api-server): add multimodal image attachment support for session chat
aea9f5f48 fix(api-server): remove self. from module-level cron function calls
f3f382e5f fix(api-server): add SSE keepalive to session chat/stream endpoint
d4e642904 fix(api-server): drop skills_categories removed by upstream 8d023e43
629e1ec70 fix(web-server): require bearer auth on /api/plugins/* routes
73babf72a feat(api): add Relay-style audio endpoints
d58b797bd fix(api-server): emit live tool.started/tool.completed on session chat stream
b78288ed0 refactor(api-server): remove duplicate session routes (#3)
```

### forge

```text
b14096f91 fix: restore forge platform delivery adapter
307415786 feat(forge): streaming draft support via chat.startDraft/appendDraftChunk/finalizeDraft
d95b9381e Enforce Forge run tool policy
```

### webhook

```text
53bb3067d feat(webhook): support route-level toolsets
```

### proxy-provider

```text
c4af1eeb9 fix: guard self.model against non-string values from local provider configs
3d3679c84 fix(boot-md): resolve model/provider from config.yaml so boot agent can make API calls
97710ec71 fix(boot-md): resolve model/provider from config so boot agent can make API calls
6e3d2d2f5 feat(anthropic): Claude Max OAuth prompt shim + /auth command
c0f6da85a fix(anthropic-oauth): cap <system-reminder> injection at 12k chars
c6fa0ff72 fix(anthropic-oauth): compute billing header after system-reminder prepend
77bf5daa0 Revert "fix(anthropic-oauth): compute billing header after system-reminder prepend"
42b1900ca Revert "fix(anthropic-oauth): cap <system-reminder> injection at 12k chars"
aef6dde36 Revert "feat(anthropic): Claude Max OAuth prompt shim + /auth command"
914fce770 docs(devlog): record anthropic oauth shim cleanup
efa7532cb feat: add routed oauth proxy adapters
913d976ec docs: mark model router shelved
7c7f25d9e fix(proxy): advertise current Codex models
1ea2c2199 docs: document routed subscription proxy discovery
1482032a6 fix: gate routed proxy model inventory
9071ab811 fix: translate codex chat completions
```

### update-deploy

```text
1a41d32c6 fix(update): check upstream before declaring up-to-date, rebase local commits on upstream for forks
7244ac4ff fix(update): show copy-paste context block when rebase fails, directing user to Victor agent
b33c3f86f fix(update): detect upstream changes via HEAD diff so deps reinstall and gateway restarts after fork sync
08dbc85d7 fix(banner): check upstream remote in update check for forks, not just origin
67c281241 fix(update): support deploy branch (axiom) update strategy
4c33f6282 fix(update): clean up deploy branch output formatting and error prompts
d418e52e3 fix(cli): deploy branch awareness for version and update check
657b72f73 fix(cli): print rescue prompt for stash restore conflicts
099459f97 fix(update): restart hermes-dashboard services alongside gateway after update
6d77e6bf1 feat(cli): add neural-memory update to hermes update flow
840bc1a3e fix(update): resolve Lucid dashboard compose path after rebrand
cb952db66 feat(update): pipeline TUI + agent-readable changelog brief
c76f2407d feat(update): print brief digest inline + inject agent summary in-channel
eb17fb4bd docs(devlog): 2026-04-21 upstream sync + update UX rework
10506ab0f feat(version): preview pending upstream commits so you can decide to update
ba5d2bffb docs(devlog): 2026-04-21 evening — version preview, multi-profile restart, env-leak fix
56043d1bc fix(dashboard): suppress bundled example sidebar plugin
a42f1dca6 chore(update): drop stale _update_neural_memory hook
3c00532aa fix(update): exclude systemd-managed dashboards from post-update kill sweep
6aefb0970 fix(update): restart named profile gateway services
7fe6222e6 fix(update): make deploy branch updates transactional
bea52a7a7 fix(update): report deploy branch status from origin
8fd4ba8fe fix(update): restore concurrent instance helper
7bbf74e0d fix(update): preserve Windows quarantine safeguards
0c3a6261c fix(update): harden deploy branch reconciliation
d902c5669 test(update): force web build path in updater test
```

### tui-relay

```text
0fa48f73e fix(tui): align plugin command cards with router flow
e57e5c5c8 feat(tui_gateway): add image.attach.bytes RPC for remote clients
```

### tests/tooling

```text
250d30df8 test(gateway): harden axiom merge validation
```

### docs

```text
28acf4e92 docs(devlog): add hermes-agent devlog
398f06fcd docs: record axiom upstream sync
ddbcf9277 docs: record api session route cleanup
```

### misc / unknown — requires review

These commits need explicit classification before merge hardening is complete:

```text
3856bbd0c fix: handle upstream model config returning dict instead of string
73fa63742 fix: vision multimodal support, tool result preview 4000 chars, dedup tool.started events
3555d16d1 feat: restore full webapi bridge from outsourc-e for Hermes Workspace compatibility
14a83e1fb chore(axiom): restore local changes after upstream sync
5ab369dad feat(memory): harden mempalace plugin integration
fb71de1ce fix: respect suppressed_sources in resolve_anthropic_token()
8e43cfdd3 fix(neural): prefetch recall no longer inflates access counts
e76c1425b feat: add resolve_model plugin hook for intelligent model routing
31a196570 feat: add /route and /bench slash commands for gateway
b0cdacd78 feat(gateway): multi-profile restart + pipeline UX
7ce0e53d8 fix(gateway/restart): strip profile-leaky env before spawning child
8cc911b63 feat(gateway): plugin-hook slash commands + InfoCard embed system
68b651e97 feat(plugins): first-class CommandDef surface for register_command + card-aware dispatch
2fbfb3da4 feat(clipboard): pending inbox at ~/.hermes/images/inbox/
fd105066e feat: add claude-design skill
972146bb8 chore(clipboard): inbox sweep stale files during scan
91270857a chore: remove retired MemPalace memory plugin
RETIRED 2026-06-17: 11b81f69a fix: /personality now overrides SOUL.md identity via ephemeral_system_prompt priority (old axiom fork hunk; superseded by upstream dynamic ephemeral injection)
54b6087c8 fix(gateway): tolerate corrupt status files
da6e0cab6 tooling: add merge-drop auditor for upstream syncs
```

## Fork-only changed-file surface

Fork-only changed file count from merge-base to `origin/axiom`: 107 files.

Top changed directories:

```text
31 tests
20 webapi
16 hermes_cli
10 plugins
10 gateway
5  website
4  agent
3  tools
2  skills
2  scripts
1  tui_gateway
1  model_tools.py
1  docs
1  DEVLOG.md
```

Top repeatedly touched files by fork commits:

```text
23 hermes_cli/main.py
21 DEVLOG.md
16 gateway/platforms/api_server.py
10 gateway/run.py
8  agent/anthropic_adapter.py
6  run_agent.py
6  gateway/platforms/discord.py
5  hermes_cli/commands.py
4  website/docs/user-guide/messaging/discord.md
4  tests/hermes_cli/test_proxy.py
4  hermes_cli/proxy/adapters/routed.py
4  hermes_cli/plugins.py
3  tui_gateway/server.py
3  tests/agent/test_anthropic_adapter.py
3  hermes_cli/web_server.py
3  hermes_cli/update_ui.py
3  hermes_cli/banner.py
3  gateway/platforms/base.py
```

Hotspot interpretation:

- `hermes_cli/main.py` is the highest-risk file because upstream is actively extracting CLI subcommands from it.
- `gateway/platforms/api_server.py` is high-risk because upstream has landed baseline session APIs that overlap Axiom/Hermes-Relay compatibility routes.

## Required validation before merging upstream into `origin/axiom`

Run from the candidate merge worktree using the repo venv:

```bash
python -m py_compile \
  gateway/platforms/api_server.py \
  gateway/run.py \
  hermes_cli/main.py \
  hermes_cli/plugins.py \
  hermes_cli/commands.py \
  hermes_cli/web_server.py \
  tui_gateway/server.py \
  model_tools.py \
  agent/runtime_tool_policy.py \
  agent/moa_loop.py \
  agent/auxiliary_client.py
```

Focused test suite:

```bash
python -m pytest -q -o addopts='' \
  tests/gateway/test_api_server.py \
  tests/gateway/test_session_api.py \
  tests/gateway/test_api_server_runs.py \
  tests/gateway/test_api_server_toolset.py \
  tests/agent/test_runtime_tool_policy.py \
  tests/test_model_tools.py \
  tests/tools/test_delegate.py \
  tests/gateway/test_forge_plugin.py \
  tests/gateway/test_webhook_adapter.py \
  tests/hermes_cli/test_webhook_cli.py \
  tests/gateway/test_reasoning_command.py \
  tests/gateway/test_discord_allowed_mentions.py \
  tests/gateway/test_discord_send.py \
  tests/hermes_cli/test_proxy.py \
  tests/agent/test_anthropic_adapter.py \
  tests/run_agent/test_moa_loop_mode.py \
  tests/agent/test_auxiliary_main_first.py \
  tests/hermes_cli/test_moa_config.py \
  tests/gateway/test_moa_one_shot_restore.py \
  tests/hermes_cli/test_plugins.py \
  tests/tui_gateway/test_protocol.py \
  tests/hermes_cli/test_update_check.py \
  tests/hermes_cli/test_update_autostash.py \
  tests/hermes_cli/test_update_stale_dashboard.py \
  tests/hermes_cli/test_cmd_update.py
```

Add or fix tests before merge if any protected behavior is not covered.

## Refactor hardening targets

The preferred direction is to move Axiom-only behavior out of upstream churn zones:

```text
hermes_cli/main.py                 -> hermes_cli/subcommands/* or small update/deploy modules
gateway/run.py                     -> gateway mixins/plugins/hooks where possible
gateway/platforms/api_server.py    -> webapi/* compatibility modules and explicit route registrars
tui_gateway/server.py              -> protocol handlers/modules
gateway/platforms/discord.py       -> platform plugin / adapter helper seams where upstream-safe
```

Candidates for upstream PRs or upstream-compatible plugin seams:

```text
route-level webhook toolsets
API route duplicate registration guard
SSE keepalive and live tool event streaming
transactional fork/deploy branch update behavior
failed-update rescue prompt / handoff context
Forge-style run tool policy, if generalized
plugin command card metadata and TUI rendering
```

## Open questions

1. Which Axiom API compatibility routes are still required by active Hermes-Relay/Desktop/Workspace clients?
2. Which fork-only update/deploy behaviors are still needed now that upstream has refactored CLI subcommands?
3. Should `webapi/*` remain fork-owned, be retired in favor of upstream API server behavior, or become a plugin/module seam?
5. Should routed OAuth proxy adapters stay fork-local or be proposed upstream?
6. Which `misc/unknown` commits are dead code, config-only candidates, or protected behavior?
