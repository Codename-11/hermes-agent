# Hermes Agent — Dev Log

## 2026-08-22 — Scope Desktop projects to the selected gateway

### Summary

Kept the grouped Projects/Recent Sessions overview while making gateway
selection the hard project-data boundary. `This device` no longer retains or
fans in projects from a previously selected or merely registered remote.

### Details

- Removed the old profile-pinned remote project fan-out policy. All Profiles
  means every profile hosted by the selected gateway, not every registered
  gateway.
- Added project state to the established soft gateway-switch wipe, including
  request-generation invalidation so late responses from the previous gateway
  cannot repaint its tree.
- Added an explicit project list/tree reload after the new profile settles. The
  connection atom can trigger the sidebar refresh before the wipe; relying on
  that effect left the newly selected remote empty after stale state was removed.
- Made project overview authority gateway-wide: one selected-gateway REST
  aggregate includes every profile hosted there, regardless of selected/focused
  chat profile. This avoids both focused-socket rejection and per-profile empty
  trees while preserving strict local-vs-remote isolation.
- Preserved project overview presentation for empty/loading trees, keeping flat
  sessions under a separate Recent Sessions heading.
- Kept Hybrid Projects removed from Axiom Enhancements; this is a narrow core
  sidebar/state carry because the full project feature set is core-owned.

### Verification

- Gateway-switch wipe regressions: 2 passing.
- Connection selection/reload regressions: 12 passing.
- Combined project/connection/gateway-switch/hybrid-renderer regressions: 67 passing.
- Selected-gateway project regressions: 2 passing.
- Projects/Recent Sessions presentation regressions: 5 passing.
- Packaged and hash-verified Desktop `aedebecbdb`; operator-confirmed live behavior:
  This Device excludes remote projects, Docker-Server loads its gateway-wide
  project aggregate, the loader settles, and normal sessions remain under
  Recent Sessions.

## 2026-08-19 — Move detailed fork disparity into Axiom Enhancements

### Summary

Made Axiom Enhancements the sole detailed deploy/upstream/local update cockpit
while keeping generic update availability and actions in Desktop core.

### Details

- Backend update translation now preserves `upstreamAhead` through the typed
  `host.updates` snapshot alongside the existing upstream/deploy fields.
- Core About, statusbar, and update overlay no longer render branch-specific
  carried/behind or reconciliation detail.
- The shared version resolver now describes only generic version, commit-diff,
  update, and restart state; generic overlay sizing, scrolling, changelog, and
  update actions remain unchanged.
- Axiom Enhancements v0.5.0 renders `+N carried`, `N awaiting reconciliation`,
  and aligned states in its three-layer Update Control view.

### Verification

- Focused Desktop update/SDK/version/statusbar regressions: 76 passing.
- Desktop renderer/Electron/E2E TypeScript typecheck, changed-file ESLint, and
  production build.
- Axiom Enhancements contract smoke and 33 focused tests; Agent Library catalog
  validation.

## 2026-08-19 — Restore browse-owned profile selection after source routing

### Summary

Restored the Desktop profile rail's selected state after the source/profile
routing campaign reintroduced gateway-owned highlighting for shared remotes.

### Details

- Profile rail highlighting and the condensed picker follow the explicit
  sidebar browse scope; gateway state remains authoritative only for request and
  socket routing.
- Relative profile keyboard navigation advances from the browsed profile rather
  than the shared primary socket owner.
- Added a regression for the keyboard sibling path and retained the existing
  browse-scope authority tests.
- Kept the invariant in Desktop core. Axiom Enhancements owns optional
  presentation settings and Update Control UI, not profile/session routing.

### Verification

- Focused profile state and browse-scope regressions: 17 passing on
  Axiom-Desktop.
- Desktop renderer/Electron/E2E TypeScript typecheck.
- Changed-file ESLint was unavailable because the Windows root dependency
  install could not resolve `eslint`.

## 2026-08-17 — Restore plugin pane exits and saved cold-start skins

### Summary

Kept closeable runtime-plugin panes escapable, migrated the Bots pane to its corrected Sessions-bottom dock, and stopped delayed backend skins from being discarded during Desktop cold start.

### Details

- Lone closeable plugin panes now retain standard tab chrome, while every visible pane body exposes the generic zone context menu and its Close action without overriding pane-owned or Electron-native edit/media menus.
- The Bots plugin uses a fresh local pane ID so persisted pre-dock layouts adopt the existing `placement: left`, 260 px width, and Sessions-bottom dock without resetting unrelated layout.
- Persisted backend/contributed skin slugs survive the provisional boot paint and repaint as soon as gateway theme seeding registers the saved skin; interactive selections remain registry-validated.

### Verification

- Focused pane chrome, guarded body-menu, Bots migration, and delayed-theme regressions: 32 passing.
- Desktop renderer/Electron/E2E TypeScript typecheck and changed-file ESLint.
- Full production Desktop build, including Electron main/preload bundles and native dependency staging.

## 2026-08-14 — Route aliased remote session resumes correctly

### Summary

Restored writable Desktop sessions when a local profile handle targets a differently named remote profile.

### Details

- Gateway RPCs now translate the active local profile handle to the connection's effective remote profile before dispatch.
- Main chats and session tiles share the correction through the central request path.
- Explicit sibling-profile requests remain unchanged, and transport recovery reuses the already-routed parameters without adding retries or duplicate prompts.

### Verification

- Focused gateway, composer, tile, and shared-remote routing regressions: 32 passing.
- Desktop renderer/Electron/E2E TypeScript typecheck, changed-file ESLint, and production build.

## 2026-08-13 — Keep live-turn status visually online during quiet work

### Summary

Stopped long-running Desktop sessions from appearing to flicker offline and online whenever the internal quiet-turn watchdog changed state.

### Details

- `working` and watchdog-quiet `stalled` turns now render the same solid accent status dot because both remain authoritatively running.
- The internal stalled state and running-arc behavior remain intact for diagnostics without presenting a false connectivity transition.
- Added a regression that locks the two live-turn states to one user-facing dot treatment.

### Verification

- Focused session status, watchdog, and dot-priority regressions: 29 passing.
- Desktop renderer/Electron/E2E TypeScript typecheck and changed-file ESLint.

## 2026-08-13 — Stabilize Desktop cold-start profile restoration

### Summary

Stopped the primary Desktop window from parking on “Waking up Victor” and resetting appearance to default light while helper session windows worked normally.

### Details

- Redundant same-profile OAuth overrides that resolve to the app-wide remote gateway now share the healthy primary socket instead of dialing duplicate secondary WebSockets; different URLs, path prefixes, auth shapes, and remote aliases remain isolated.
- Renderer cold start now distinguishes the synthetic initial `default` profile from the first authoritative gateway profile.
- Profile-scoped theme and mode restoration keep using the persisted boot profile until gateway adoption completes, so startup cannot overwrite the last-active appearance slot with default light.
- Helper windows honor their explicit query-string profile from first paint.

### Verification

- Focused remote-route and OAuth regressions: 90 passing.
- Focused appearance/profile persistence and routing regressions: 34 passing.
- Desktop renderer/Electron/E2E TypeScript typecheck and whitespace checks.

## 2026-08-13 — Serialize Desktop native OAuth refresh rotation

### Summary

Stopped intermittent remote Desktop startup, session, and model-catalog failures caused by concurrent native OAuth refreshes and silent cookie fallback.

### Details

- Native token refreshes now single-flight per normalized gateway base URL, so pooled profile liveness checks share one rotating refresh-token exchange without crossing path-prefixed deployments.
- Waiting callers consume the stored rotated access token instead of replaying the previous refresh token.
- Transient refresh failures now remain transport failures across readiness, ticket minting, profile discovery, generic REST, and management IPC paths; only an absent native session or explicit terminal refresh rejection may select legacy cookie auth.
- Failed refresh promises are removed from the coordinator so later liveness checks can recover.

### Verification

- Focused native-auth, profile-auth, WebSocket ticket, and refresh-coordinator regression tests.
- Desktop Electron/UI/E2E TypeScript typecheck, changed-file ESLint, and production build.

## 2026-08-13 — Preserve validated deploy resolutions across push failures

### Summary

Made the deploy updater treat a failed post-resolution push as a resumable
publication failure rather than another merge-resolution problem.

### Details

- Handoff schema 3 records `push_pending`, the exact full resolved commit ID,
  and a bounded, force-redacted Git diagnostic tail.
- The next `hermes update` retries that exact commit directly and never reruns
  the resolver agent; a changed retained HEAD stops safely.
- Failure output now includes the useful multi-line Git reason plus a focused
  fresh-chat command, while leaving the live checkout untouched.

### Verification

- Full deploy updater and command-level update pytest suites.
- Python syntax compilation, whitespace/conflict-marker checks, and focused
  handoff state-machine invariants.

## 2026-08-13 — Qualify every mixed-profile Desktop navigation path

### Summary

Closed four late-review identity gaps left after the shared mixed-profile chat
workspace landed. Keyboard switching, session pop-out windows, command-palette
rows, and helper-window workspace restore now preserve the owning profile when
cloned profiles share a stored session ID.

### Details

- Session switcher quick-jump, delayed commit, and numbered slots now return
  `(profile, sessionId)` targets instead of bare IDs.
- Session pop-out IPC carries the profile through preload and Electron into the
  renderer URL, and the native window registry uses a profile-qualified key.
- Command-palette row IDs are profile-qualified, preventing duplicate cmdk values
  and React keys for cloned-profile sessions.
- Window-owned workspace atoms bind to the query-string startup profile once,
  while remaining stable across later live gateway/profile routing changes.

### Verification

- Desktop renderer/Electron/E2E TypeScript typecheck.
- Focused UI and Electron regression suites for all four identity boundaries.
- Changed-file ESLint and production Desktop build.

## 2026-08-13 — Make Desktop chat workspaces profile-mixed and owner-routed

### Summary

Changed Desktop from profile-swapped workspaces to one window-owned pane layout
whose chat tabs carry explicit `(profile, storedSessionId)` identity. Chats from
multiple profiles can now coexist without replacing the tab strip or sidebar
browse scope, while each visible pane routes resume, model, prompt, and session
actions through its owning profile connection.

### Details

- Migrated persisted session-tile descriptors and pane IDs to profile-qualified
  identities, including cloned profiles that share stored session IDs.
- Kept sidebar browse scope separate from active gateway routing, so focusing a
  cross-profile chat no longer changes the sidebar or swaps the pane layout.
- Preserved owner profile identity in chat headers and existing pane-header
  right-click visibility controls.
- Kept pooled gateways alive for every open chat profile, preventing the repeated
  reap/reconnect cycle that produced avoidable spinners and tab-switch latency.
- Fixed intermittent model-picker loading by aligning its query key and RPC with
  the owning tile profile instead of a stale globally active gateway; retries are
  bounded and picker remounts do not force redundant catalog refetches.
- Retired legacy tile persistence after one-shot migration, and made optimistic
  session-row replacement profile-qualified so cloned-profile siblings survive.

### Verification

- Desktop renderer/Electron/E2E TypeScript typecheck.
- Focused mixed-profile, tile lifecycle, model-menu, workspace persistence, and
  session-action tests.
- Changed-file ESLint and production renderer build.

## 2026-08-13 — Stream upstream reconciliation output inside Update Control

### Summary

Closed the observability gap left by the first Update Control pass: the
reconciliation transcript had been attached only to the terminal Promise
result, so no panel could appear while the long-running sync was active.

### What changed

- Electron now retains a bounded, sanitized upstream-sync status snapshot while
  the subprocess is running and keeps the final result after exit.
- Added a read-only status IPC/preload/store/SDK path outside the exclusive
  update-operation lock, allowing the renderer to observe rather than contend
  with the active mutation.
- Update Control polls that snapshot once per second only while sync is active,
  reattaches after pane remounts, and retains the final transcript.
- The Reconcile CLI output disclosure opens immediately when Sync begins and
  shows `Waiting for CLI output…` until the first subprocess line arrives.

### Verification

- Focused Electron tests: 8 passed.
- Focused SDK/Update Control renderer tests: 12 passed.
- Full Desktop TypeScript check and changed-file ESLint passed.
- Production renderer, Electron main/preload, and native dependency staging
  build passed.

## 2026-08-13 — Make Desktop Update Control observable and explicitly recheckable

### Summary

Fixed false failures in the source-owned Desktop Update Control path and exposed
bounded CLI output for Desktop staging, upstream reconciliation, and connected
backend updates. The interface now separates a local cache refresh from an
authoritative source recheck for either target.

### What changed

- Raised the isolated upstream reconciliation safety ceiling from 10 to 30
  minutes and preserved a sanitized 24 KB subprocess tail on success, failure,
  missing receipts, spawn errors, and timeouts.
- Removed the renderer's six-minute deadline while a durable backend action
  receipt still reports `running`; an actually unreachable backend retains the
  existing bounded four-minute recovery window.
- Kept backend action output after completion and normalized it through the
  plugin SDK alongside a guarded tail of the core-owned Desktop staging log.
- Added collapsed-by-default `Desktop CLI output`, `Reconcile CLI output`, and
  `Backend CLI output` views using the native `LogView` component.
- Split the old ambiguous check action into `Refresh view` (cached query state)
  and explicit `Recheck Desktop` / `Recheck Backend` source operations; contextual
  buttons now use the same `Recheck source` wording.

### Verification

- Focused Electron and renderer suites: 62 tests passed.
- Full Desktop TypeScript project check passed.
- Changed-file ESLint passed with zero warnings or errors.
- Production renderer, Electron main/preload, and native dependency staging
  build passed.
- Full-repo lint remains red on pre-existing errors outside this change.

## 2026-08-11 — Harden Desktop session convergence across reconnects and profiles

### Summary

Closed the cross-layer races that let transcript hydration, gateway reconnects,
runtime reclamation, and live-status snapshots drift apart while switching
chats or hot-swapping profiles. The protected carry is implemented by
`6616b11bef` and follow-up `7d37b0ef4a`.

### Protected behavior

- Dispatch-time gateway/profile ownership fences retries and asynchronous
  transcript/todo/status work from mutable foreground selection.
- Primary and secondary reconnects reconcile the exact gateway's
  `session.active_list` through the canonical runtime cache; stale snapshots
  cannot overwrite newer local stream edges.
- Idle, vanished, and reclaimed runtimes fully settle or evict public/private
  status, stream, todo, and reverse-mapping state.
- Non-primary connection applies recreate the renderer-owned profile socket.
- Running/attention identity and unscoped stream pins are profile-qualified, so
  cloned profiles may safely contain the same stored session id.

### Verification

- Full Desktop UI suite: 3,775 tests passed.
- Full Electron suite: 1,075 passed, 2 skipped.
- Follow-up focused status/reconnect suite: 61 tests passed.
- Desktop TypeScript, production build, changed-file ESLint, and diff checks passed.
- Detailed protected files, upstream overlap (`#45653`, `#71475`, `#51058`),
  focused commands, and drop conditions are recorded in `FORK.md`; the concise
  operator contract is in `docs/axiom-fork-contract.md`.

## 2026-08-11 — Project previews, worktree lifecycle, and rebindable voice controls

### Summary

Completed the follow-up Axiom Desktop slice: restored bounded Project/Home quick
previews without sacrificing full drill-in, made Project/Home ownership explicit
and reassignable, exposed Project/worktree session lifecycle actions, and added
separate unbound Desktop actions for dictation, Read replies aloud, and Hey Hermes.

### Protected behavior

- Project/Home chevrons persist independently and render at most five recent
  sessions, always retaining the active conversation; `View all N sessions`
  remains a separate drill-in action.
- Membership now reads the complete compact eligible-session set before any
  preview limit, so old sessions cannot silently fall out of their Project.
- Global rows and the focused statusbar show textual Project/Home identity.
  Reassignment shows the current owner and can legitimately unassign to Home
  while retaining a valid backend cwd and clearing stale Git metadata.
- Project overview/drill-in and the focused coding row expose distinct main
  checkout, new isolated worktree, current-session retarget, existing-worktree,
  and return-to-Project actions through existing Git/worktree primitives.
- Dictation uses the active-composer event bus, so one binding starts/stops only
  the visible owner and respects disabled, unavailable, and transcribing states.
- Read replies aloud writes the existing profile `voice.auto_tts` setting through
  its optimistic/revert persistence path; keyboard failures produce a toast.
- Hey Hermes still delegates mic leases, capture mode, pending state, and
  persisted truth to the gateway; keyboard refusals/failures are visibly reported.
- All three buttons discover configured bindings in tooltips and expose
  `aria-keyshortcuts`; all required Desktop keybind locales include action labels.
- Registry, routing, persistence/error, wake success/refusal/failure, tooltip,
  and accessibility behavior have focused regression coverage.

### Verification

- Integrated verification evidence is recorded in the implementation handoff.

## 2026-08-10 — Carry the hybrid Desktop Projects overview

### Summary

Kept Projects primary in grouped Desktop mode while restoring a separate flat
Recent Sessions lane beneath it, and added explicit-submit-only creation for
typed project directory paths in both local and authenticated remote Desktop.
Projects/Home and flat recents now also share a deliberate source taxonomy so
messaging and automation history no longer pollute local project navigation.

### What changed

- Removed nested three-session previews from project rows; the overview now
  reuses the existing flat session rows, actions, date/status grouping, filters,
  ordering, and pagination under a dedicated Recent Sessions heading.
- Kept global recents out of project drill-in and left flat Sessions mode
  unchanged. Project/worktree lane paging now has a visible `Show N more` label.
- Made Home an explicit creation target: selecting it clears the active project,
  marks Home active, and restores the project-row `+` affordance for a no-folder
  new session.
- Added a typed directory field to Create Project. Submit resolves/creates the
  directory through Electron locally or the active profile's authenticated
  `/api/fs/ensure-directory` route remotely; typing and browsing do no writes,
  and failures stay visible without creating the project.
- Replaced the project tree's `cron`/`kanban` denylist with an authoritative
  backend allowlist of interactive local conversation sources. Discord,
  Telegram, A2A, webhook/API, cron, kanban, subagent/tool, and unknown future
  system sources fail closed from Projects/Home while remaining in their own
  history/search surfaces.
- Centralized Desktop's automation-source taxonomy and reused it for the flat
  Recents request, eliminating the separate A2A/API/webhook leak there without
  mutating existing database rows or relying on titles.
- Added focused renderer/store/filesystem and authenticated backend regression
  coverage. The carry and its complete drop criteria are recorded in `FORK.md`
  and `docs/axiom-fork-contract.md`.

### Verification

- Focused Desktop UI/store/filesystem/source-policy suite: 75 passed.
- Authenticated backend filesystem suite: 5 passed.
- Desktop typecheck: passed.
- Project-tree source policy + Projects RPC suite: 25 passed.
- Desktop source-policy/refresh slice: 17 passed (included in the 75 above).
- Full Desktop lint remains red on pre-existing errors outside this carry;
  focused changed-file lint is recorded in the implementation handoff.

## 2026-08-03 — Recover unscoped cross-profile session reads

### Summary

Restored Discord transcript loading for older Desktop request paths that selected a row from the cross-profile messaging list but omitted its owning `profile` from subsequent session detail/message reads.

### What changed

- Unscoped read-only session detail, transcript, and latest-descendant endpoints now fall back across named profile databases only when the requested ID has exactly one owner.
- Explicitly scoped requests remain authoritative, and ambiguous IDs still return not found; mutation endpoints do not cross profile boundaries.
- Added regression coverage for both unique-owner recovery and collision rejection.

### Verification

- Focused endpoint suite: 5 passed.
- Live unscoped read of Mizu Discord session `20260803_085708_54d1eac3` resolved to profile `mizu` and returned 243 messages.

## 2026-08-02 — Retire the A2A carry into upstream v1

### Summary

Merged upstream's accepted A2A v1 implementation from PR #77109, retiring Axiom's temporary PR #41711 plugin carry while preserving the generic plugin-loader behavior required for enabled platform plugins that also expose tools.

### What changed

- Replaced `plugins/platforms/a2a/*` and the original A2A test file with upstream's protocol-v1 implementation and added upstream's Phase 2/3 coverage.
- Retired Axiom's notice-prefix workaround in favor of upstream's gateway `metadata["notify"]` final-reply contract.
- Preserved `9c06b9874`, which eagerly loads explicitly enabled bundled platform plugins so their toolsets register and appear in `hermes tools`.
- Marked the old carry, TGI overlay, and #41711 watcher for retirement rather than continuing to treat them as protected fork surface.

## 2026-08-02 — Stream auto-resolver progress without weakening validation

### Summary

Restored live visibility into deploy-branch conflict resolution. The autonomous Hermes resolver now uses visible non-interactive chat and streams its transcript to the terminal and messaging `/update` output instead of leaving the operator at a silent `agent resolve` phase for the duration of the session.

### What changed

- Replaced the final-response-only `hermes -z` child with non-interactive `hermes chat -q` plus a line-buffered tee that streams tool progress and merged stdout/stderr while retaining a bounded transcript tail for failure diagnostics.
- Framed the child session as advisory and kept parent-side conflict-marker checks, focused verification, commit, push, and live fast-forward as the authoritative result.
- Kept the one-command auto-resolve workflow and all existing sensitive-path and live-checkout isolation gates.

## 2026-07-25 — Collapse deploy updates to one command

### Summary

Removed the deploy-only `--resolve` / `--consume` mode split. Bare `hermes update` now performs the complete fork workflow from either Axiom host: fetch both remotes, reconcile upstream in a temporary worktree, auto-resolve and validate conflicts when required, publish `origin/<deploy>`, fast-forward live, reinstall/restart normally, and print the existing categorized update brief.

### What changed

- Removed the CLI and messaging mode flags; `/update` and `hermes update` now expose one behavior.
- Kept live checkout isolation, sensitive-path gates, focused checks, push-race recovery, phase-by-phase resolve status, and post-update change briefs.
- Ignored update recovery markers and removed the accidentally tracked `.lazy-refresh-incomplete` artifact so resolver runs cannot publish transient runtime state again.
- Reframed deploy ownership around the first host to observe upstream work rather than a permanent server/client authority split.

## 2026-07-22 — Merge upstream and separate runtime deploys from integration

### Follow-up: refresh deploy artifact before comparison

The first Windows consume verification exposed a second bug: Desktop fetched `upstream` but compared against a stale local `origin/axiom`. GitHub already had the server's resolved deploy artifact, while Desktop still held the old remote-tracking ref and therefore falsely reported “origin current” plus hundreds of upstream commits pending.

`_run_deploy_branch_update()` now explicitly fetches `origin/<deploy>` before computing deploy/upstream counts. A regression test models a stale ref that changes only after this fetch and requires the client to consume the newly discovered deploy commits. Focused update coverage passes with 150 tests.

### Summary

Merged 286 upstream commits into the `axiom` deploy branch, preserved Axiom fork contracts across six conflict files, and changed plain deploy-branch `hermes update` into a consume-only runtime operation. Upstream integration now requires explicit `hermes update --resolve` authority or the dedicated sync workflow, so a routine update cannot unexpectedly become a semantic merge session.

### What changed

- Resolved upstream conflicts in the Anthropic adapter, secret scoping, Desktop Electron startup/auth, gateway transport base, CLI update/restart flow, and web-server download/voice routes.
- Preserved Axiom's assistant-message mutation tracking while incorporating upstream's required leading user turn; shifted mutation indices when that synthetic turn is inserted.
- Preserved all Axiom gateway/dashboard service discovery while adopting upstream's bounded per-unit restart helper.
- Retained both Axiom's native-auth Desktop path and upstream's binary download behavior.
- Fixed the carried Desktop model-controls regression test to use the new profile-aware model-options query key and removed its stale unused local.
- Added `_resolve_deploy_update_modes()` in the fork-owned `hermes_cli/axiom_update.py` seam plus regression tests: plain updates consume, `--resolve` grants merge authority, and `--resolve --consume` is rejected.
- Updated `FORK.md`, `docs/axiom-fork-contract.md`, and the canonical Obsidian Axiom sync/overview notes for the authority split.

### Verification

- Python focused update/CLI suite: 203 passed.
- Python adapter/secret/gateway/web-server suite: 920 passed, 9 deprecation warnings.
- Desktop full suite: 2,631 passed, 3 skipped.
- TUI `npm run check`: build, typecheck, and 1,347 tests passed, 1 skipped.
- Desktop TypeScript typecheck and Electron native-auth/OAuth focused tests passed.
- Live deploy verification: `HEAD == origin/axiom` at `b8df9603ca4c`, `upstream/main` is contained with zero commits pending, the checkout is clean, all five profile gateways are active, API health is `ok`, dashboard redirects normally to auth, and Hermes Proxy `/v1/models` returns HTTP 200.
- Interactive `hermes` rendered the TUI successfully under a bounded PTY smoke with no traceback or startup error; the timeout terminated the intentionally interactive process after rendering.
- The stale profile-scoped handoff marker and its retained merge worktree were removed only after the ancestry/live-origin checks passed.

## 2026-07-14 — Expose mode-safe xAI 1080p image-to-video

### Summary

Added native `1080p` requests for fresh `grok-imagine-video-1.5` image-to-video generation while keeping xAI extension and unproven model/modality paths fail-closed at their documented limits.

### What changed

- Added `1080p` to the xAI provider capability surface.
- Accepts it only for exact-model fresh image-to-video requests using `grok-imagine-video-1.5`.
- Rejects 1080p text-to-video, reference-to-video, legacy/alias models, and extension requests before network submission.
- Clarified that video extension inherits source resolution and remains capped at 720p.
- Added provider, integration, and tool-surface regression coverage while preserving existing 480p/720p behavior.

### Verification

- Focused video-provider/tool suite after rebasing onto current `origin/axiom`: 101 passed.
- Python compilation and `git diff --check`: clean.
- No media request, gateway restart, service reload, commit push, or production generation was performed during verification.

## 2026-07-14 — Restore generic named-profile cron execution

### Summary

Removed the deployment-specific `victor`→`default` cron owner alias and restored Axiom's generic owner-home execution carry after an upstream merge regressed the shared-registry contract.

### What changed

- Cron owner normalization now aliases only missing/`root` ownership to `default`; every valid named profile, including `victor`, remains distinct.
- Restored the shared-root tick lock for Axiom's single cross-profile registry.
- Restored owner-profile script resolution and `HERMES_HOME` propagation for script-only, pre-run-script, and agent cron jobs.
- Serialized owner-home environment overrides with the existing scheduler environment lock and restored the prior environment after each run.
- Added regression coverage proving a profile literally named `victor` resolves to `profiles/victor`.
- Migrated Docker-Server's eight Victor cron rows from `default` to `victor` and added the missing Victor-local weekly-backup launcher.

### Verification

- Named-Victor regression was red before the fix (`default != victor`) and green after it.
- `tests/cron/test_cron_profile_storage.py`, `test_cron_profile_isolation.py`, and `test_cron_script.py` → 55 passed.
- Profile tests plus multiplex secret-scope regression → 56 passed.
- Full `tests/cron` with an isolated temporary `HERMES_HOME` after rebasing onto the latest `origin/axiom` → 702 passed, 2 warnings.
- Live checks: the shared registry remained stable at 8 Victor / 9 Sentinel rows across a full scheduler minute after all six profile gateways reloaded; Victor sees exactly its eight jobs and explicit default sees zero. The weekly-backup job ran through Victor's profile-local launcher and created a 3.5 GB archive; the Relay watcher ran silently without a script-resolution failure.
## 2026-07-14 — Isolate large MCP schemas behind catalog profiles

### Summary

Separated MCP connectivity from model-visible schema exposure so large
catalog-capable integrations no longer inject their full workflow vocabulary
into ordinary sessions by default.

### What changed

- Added `mcp_servers.<name>.exposure` (`auto`, `catalog`, `direct`, `off`) and
  optional platform scoping through `expose_on`.
- Large servers with a complete three-operation catalog bridge now expose only
  that bridge by default; deferred operations live in an explicit
  `<server>-direct` session/tool profile.
- Hardened platform MCP allowlists and `no_mcp` so direct profiles cannot
  bypass platform scope or accidentally pull in unrelated servers.
- Added provider-facing schema-size/vocabulary regression coverage and
  operator configuration documentation.

### Verification

- See AXI-103 PR verification for focused MCP/platform tests and the real
  provider-facing tool-list before/after measurement.


## 2026-07-13 — Add provider-pluggable music generation plugin

### Summary

Installed a standalone default-profile `generate-music` plugin that exposes one stable `generate_music` tool while keeping MiniMax behind a replaceable provider adapter.

### What changed

- Added the filesystem-local plugin under `~/.hermes/plugins/generate-music/` with a provider protocol/registry, MiniMax `music-2.6-free` adapter, bounded JSON/SSE/raw-audio handling, profile-scoped artifacts, safe filenames/extensions, and `MEDIA:/absolute/path` responses.
- Kept credentials out of plugin config: the MiniMax adapter reads `MINIMAX_API_KEY`; nonsecret provider/model/output/timeout/size settings live in the plugin's `config.yaml`.
- Removed the generic tool's MiniMax-specific environment gate so future provider adapters can substitute without changing the model-facing tool contract.
- Enabled `generate-music` and the `music_generation` toolset for Victor's CLI and Discord surfaces; no Hermes core source change was required.
- Added plugin README/extension guidance and updated canonical Obsidian Hermes inventory/project notes.

### Verification

- `python -m pytest -q -o addopts='' ~/.hermes/plugins/generate-music/tests` → 8 passed.
- `python -m py_compile` across plugin modules → OK.
- Fresh-process plugin discovery registered `generate_music`; `get_tool_definitions(["music_generation"])` exposed exactly that tool and a terminal-only toolset did not expose it.
- `hermes config check` passed and detected `MINIMAX_API_KEY` without printing its value.
- Real `music-2.6-free` request reached MiniMax but returned status `2061` (`current token plan not support model`), so live audio/file/ffprobe verification remains blocked on a regular API key entitled to the free model. No paid-model fallback was used.

## 2026-07-07 — Route webhook cross-platform media to delivery target

### Summary

Fixed webhook routes that deliver responses to another platform (for example `deliver: discord`) so post-stream `MEDIA:` attachments use the same destination adapter as the text response instead of falling back to the origin webhook adapter.

### What changed

- Split webhook cross-platform delivery target resolution into a reusable resolver shared by text and media delivery.
- Added a webhook media-target resolver that returns the target adapter, chat ID, thread metadata, and platform.
- Updated post-stream media dispatch to send image batches, videos, voice/audio, and documents through the resolved target adapter while preserving delivery-target thread metadata.
- Added regressions proving webhook media follows the configured cross-platform target and does not call the webhook adapter's medialess sender.

### Verification

- `python -m py_compile gateway/platforms/webhook.py gateway/run.py tests/gateway/test_webhook_adapter.py` → OK.
- `python -m pytest -q -o addopts='' tests/gateway/test_webhook_adapter.py::TestDeliverCrossPlatformThreadId tests/gateway/test_webhook_adapter.py::TestWebhookCrossPlatformMediaDelivery --tb=short` → 5 passed.
- `python -m pytest -q -o addopts='' tests/gateway/test_webhook_adapter.py --tb=short` → 83 passed.

## 2026-06-27 — Preserve Anthropic provider identity in MoA slots

### Summary

Fixed MoA reference/aggregator slot runtime resolution so Anthropic slots keep `provider=anthropic` instead of being downgraded to `custom` when `resolve_runtime_provider()` supplies `https://api.anthropic.com` plus credentials. The downgrade bypassed the native Anthropic auxiliary/OAuth request-shape path even though direct Hermes Claude chat worked.

### What changed

- Added `anthropic` to MoA's provider-identity preservation set alongside `openai-codex` and `xai-oauth`.
- Added a regression test proving Anthropic MoA slots return only `{provider, model}` and let `agent.auxiliary_client` resolve the native provider branch.
- Updated `FORK.md` so the deploy-branch contract protects OAuth/adapter-backed MoA slot identity and includes MoA tests in the required validation block.

### Verification

- Regression was red before the fix: `tests/run_agent/test_moa_loop_mode.py::test_moa_anthropic_slot_preserves_provider_identity` failed because `base_url`/`api_key` leaked into the slot runtime.
- `python -m py_compile agent/moa_loop.py tests/run_agent/test_moa_loop_mode.py` → OK.
- `python -m pytest -q -o addopts='' tests/run_agent/test_moa_loop_mode.py::test_moa_anthropic_slot_preserves_provider_identity tests/run_agent/test_moa_loop_mode.py::test_moa_codex_slot_preserves_provider_identity tests/run_agent/test_moa_loop_mode.py tests/agent/test_auxiliary_main_first.py tests/hermes_cli/test_moa_config.py tests/gateway/test_moa_one_shot_restore.py` → 52 passed, 2 warnings.
- Live smoke `hermes chat --provider moa --model default ...` returned `MOA ANTHROPIC ROUTE OK`; logs showed `Auxiliary moa_reference: using anthropic (claude-opus-4-8)` and `Auxiliary moa_aggregator: using openai-codex (gpt-5.5)`.

## 2026-06-27 — Accept gateway reconnect flag in A2A and Forge plugins

### Summary

Fixed Axiom plugin adapters that were still using the pre-reconnect lifecycle signature, which caused gateway reconnect loops to log `unexpected keyword argument 'is_reconnect'` for A2A and Forge.

### What changed

- Updated `plugins/platforms/a2a/adapter.py` and `plugins/platforms/forge/adapter.py` so `connect()` accepts keyword-only `is_reconnect: bool = False`, matching the gateway/platform adapter contract.
- Added regression tests for both plugin adapters to ensure future reconnect calls continue accepting the kwarg.

### Verification

- `python -m pytest tests/gateway/test_forge_plugin.py tests/plugins/test_a2a_plugin.py -q -o 'addopts='` → 46 passed.

## 2026-06-25 — Add conflict handoff status spinner and one-shot resolver agent

### Summary

Improved deploy-update conflict UX so the delay between an initial merge failure and the printed handoff is visible, and made the autonomous resolver subprocess use Hermes' scripted one-shot path instead of chat mode.

### What changed

- Wrapped best-effort LLM conflict review generation with the scrollback-safe `StatusLine` loader (`⏳ review conflict handoff` → `✓ handoff ready`). It uses persistent Unicode phase lines instead of in-place redraws, so the user can scroll the shell normally after the command exits.
- Added the same loader coverage to `hermes update --resolve` retained-handoff execution: prepare, agent resolve, validate, focused checks, commit, push, live sync, and cleanup now emit short current-phase lines instead of the old full pipeline, avoiding line-wrap, carriage-return spam, and scrollback breakage.
- Switched the `hermes update --resolve` resolver subprocess from `hermes chat -Q -q ...` to `hermes -z ...`, avoiding TUI/session UI paths entirely and producing only the final resolver response.
- Added the `skills` toolset to the resolver subprocess and told it to load `hermes-update` via `skill_view` when available.

### Verification

- `python -m py_compile hermes_cli/axiom_update.py tests/hermes_cli/test_update_autostash.py` → OK.
- `python -m pytest -q -o addopts='' tests/hermes_cli/test_update_autostash.py tests/hermes_cli/test_cmd_update.py tests/hermes_cli/test_update_check.py tests/hermes_cli/test_version_preview.py` → 92 passed.

## 2026-06-25 — Add deploy update resolve/consume modes

### Summary

Added explicit deploy-branch update modes so operators can either authorize Hermes to resolve retained conflict handoffs unattended or keep client/Desktop installs in consume-only mode.

### What changed

- Added `hermes update --resolve` for deploy branches. It resumes an existing `.update_handoff.json` or auto-resolves conflicts encountered during the run by launching a non-interactive Hermes resolver in the retained worktree, validating no unmerged files/conflict markers remain, running focused checks, committing/pushing `HEAD:<deploy>`, fast-forwarding the live checkout, clearing the marker, and continuing the normal install/restart phase.
- Added `hermes update --consume` for deploy branches. It only fast-forwards from `origin/<deploy>` and refuses to merge `upstream/main` from the current host, so Desktop/client installs can consume the server-produced artifact without accidentally becoming merge authority.
- Enriched `.update_handoff.json` with schema, conflict files, report path, watch areas, focused checks, and ref heads.
- Expanded deploy-branch recognition to include both `axiom` and `tgi` in CLI/banner update checks.
- Updated fork contract, CLI reference docs, SYSTEM pointer, Obsidian runbook, and Hermes update skill guidance.

### Verification

- `python -m py_compile hermes_cli/axiom_update.py hermes_cli/main.py hermes_cli/subcommands/update.py hermes_cli/banner.py` → OK.
- `python -m pytest -q -o addopts='' tests/hermes_cli/test_update_autostash.py tests/hermes_cli/test_cmd_update.py tests/hermes_cli/test_update_check.py tests/hermes_cli/test_version_preview.py` → 90 passed.
- `python -m hermes_cli.main update --help` → shows `--resolve` and `--consume`.

## 2026-06-18 — Fix Windows gateway update lock ordering

### Summary

Fixed the Windows update path so Hermes pauses known gateway processes before running the generic concurrent `hermes.exe` shim guard. This lets A2A/API gateways launched as `hermes.exe gateway run` release `venv\\Scripts\\hermes.exe` before dependency reinstall, while still blocking unrelated Desktop backend/REPL processes that would keep the executable locked.

### What changed

- Moved `_pause_windows_gateways_for_update()` ahead of `_detect_concurrent_hermes_instances()` in `hermes_cli/main.py` and registered gateway resume immediately after a successful pause.
- Added Windows gateway-launcher detection for `venv\\Scripts\\hermes.exe gateway run` so the pause path force-stops the parent shim process that actually locks the executable, not only the socket-owning Python gateway PID.
- Infer the gateway profile from launcher cmdline (`--profile` / `-p`, defaulting to `default`) so manual Windows gateways are restarted after update even when PID-file mapping only sees the launcher stop.
- Kept the concurrent shim guard after gateway pause so non-gateway Hermes processes still fail safely instead of forcing WinError 32 / reboot-deferred replacements.
- Added regression coverage proving the updater pauses/registers Windows gateways before checking remaining concurrent `hermes.exe` processes.
- Updated English and zh-Hans update docs to distinguish auto-paused gateways from remaining non-gateway blockers.

### Verification

- `python -m py_compile hermes_cli/main.py` → OK.
- `python -m pytest -q -o addopts='' tests/hermes_cli/test_update_concurrent_quarantine.py` → 24 passed.
- `python -m pytest -q -o addopts='' tests/hermes_cli/test_cmd_update.py tests/hermes_cli/test_update_check.py tests/hermes_cli/test_update_autostash.py tests/hermes_cli/test_update_stale_dashboard.py tests/hermes_cli/test_update_concurrent_quarantine.py` → 124 passed.
- `python -m pytest -q -o addopts='' tests/gateway/test_planned_stop_watcher.py tests/gateway/test_gateway_shutdown.py tests/gateway/test_shutdown_forensics.py` → 58 passed, 1 existing Hermes-Relay bootstrap deprecation warning.

## 2026-06-18 — Activate A2A on Axiom-Desktop

### Summary

Used the active Hermes-Relay Desktop bridge as the remote hands path to update Axiom-Desktop's Hermes checkout, configure A2A over Tailscale, start the Windows gateway listener, and register a user-level login task for durability.

### What changed

- Fast-forwarded Axiom-Desktop's `C:\Users\Bailey\AppData\Local\hermes\hermes-agent` checkout from `d5e8b05e1` to `e7ea3213b` on `axiom`; A2A plugin files are now present and compile on Windows.
- Configured Desktop A2A endpoint `http://100.105.160.1:9900` as `victor-desktop` with bearer auth and reciprocal peers for Docker-Server and TGI Docker.
- Added `victor_desktop` peer entries to Docker-Server and TGI Docker configs. Tokens remain host-local and redacted from docs.
- Started the Desktop Hermes gateway as a hidden user process through Hermes-Relay Desktop and registered user-level Windows Scheduled Task `Hermes Gateway A2A` for login auto-start.

### Verification

- Desktop Agent Card on `100.105.160.1:9900` returned `victor-desktop`; unauthenticated JSON-RPC returned HTTP 401.
- Docker → Desktop `a2a_call` returned `DESKTOP_A2A_SMOKE_OK`.
- Desktop → Docker `a2a_call` returned `DESKTOP_TO_DOCKER_A2A_OK`.
- TGI → Desktop `a2a_call` returned `TGI_TO_DESKTOP_A2A_OK`.
- Desktop → TGI `a2a_call` returned `DESKTOP_TO_TGI_A2A_OK`.

## 2026-06-18 — Deploy A2A over Tailscale for Docker-Server and TGI

### Summary

Promoted the upstream A2A protocol carry into the live Axiom branch, fixed an integration bug where gateway/runtime notices could steal A2A replies, and deployed mutually-authenticated A2A peers between Docker-Server Victor and TGI Docker over Tailscale.

### What changed

- Merged `carry/upstream-pr-41711-a2a` into `axiom` with `--no-ff`, preserving the upstream PR carry topology.
- Added `fix(a2a): ignore gateway notices when resolving peer replies` so A2A JSON-RPC responses wait for the agent's actual answer instead of returning Hermes onboarding/advisory notices.
- Configured Docker-Server Victor A2A inbound on `http://100.71.8.56:9900` as `victor-docker` with bearer auth and `a2a_agents` entries for `victor_docker` and `tgi_docker`.
- Overlaid the A2A plugin/tests/toolset metadata onto the `tgi` branch without dragging unrelated `axiom` history, then fast-forwarded TGI Docker and configured its reciprocal A2A peer entries.
- Updated `FORK.md`, `~/SYSTEM.md`, and the Obsidian Hermes project note with live endpoint/status/verification details. Tokens remain host-local and redacted from docs.

### Verification

- Docker-Server: `python3 -m pytest tests/plugins/test_a2a_plugin.py -q -o 'addopts='` → 40 passed; `python3 -m py_compile plugins/platforms/a2a/*.py tests/plugins/test_a2a_plugin.py` → OK.
- Docker-Server A2A Agent Card on `100.71.8.56:9900` returned `victor-docker`; unauthenticated JSON-RPC returned HTTP 401; authenticated `message/send` returned `A2A_SMOKE_OK`.
- Docker-Server direct tool handler `a2a_call({'agent': 'victor_docker', ...})` returned `A2A_TOOL_HANDLER_OK`.
- TGI Docker: `./venv/bin/python -m pytest tests/plugins/test_a2a_plugin.py -q -o 'addopts='` → 40 passed; `./venv/bin/python -m py_compile plugins/platforms/a2a/*.py tests/plugins/test_a2a_plugin.py` → OK.
- Docker → TGI `a2a_call` returned `TGI_A2A_SMOKE_OK`.
- TGI → Docker `a2a_call` returned `DOCKER_A2A_SMOKE_OK`.

## 2026-06-18 — Stage A2A PR #41711 candidate tracking

### Summary

Prepared the upstream A2A protocol PR as an isolated local carry candidate and wired a quiet watcher so movement on the upstream PR is visible without polling manually.

### What changed

- Recorded upstream A2A PR #41711 as an isolated temporary carry candidate in `FORK.md` with worktree, verification, watcher, known exposure notes, and drop condition.
- Created root Hermes script-only PR watcher outside the repo at `~/.hermes/scripts/github-pr-watch.py`; live cron job `ddd8f2eaf2d6` watches PR #41711 and alerts only on movement.

### Verification

- Candidate worktree `~/.hermes/worktrees/hermes-a2a-41711-candidate/` merged PR #41711 cleanly over current `origin/axiom`.
- `python3 -m pytest tests/plugins/test_a2a_plugin.py -q -o 'addopts='` → 39 passed.
- `python3 -m py_compile plugins/platforms/a2a/*.py` → OK.

## 2026-06-17 — Consolidate fork maintenance status references

### Summary

Added a read-only fork status helper and tightened repo/Obsidian references so live branch divergence, paused Sentinel sync state, and Docker-Server/Axiom-Desktop branch expectations are generated on demand instead of copied as stale numbers.

### What changed

- Added `scripts/fork-status.py`, a read-only report for local branch state, dirty files, `origin/axiom` vs `upstream/main` divergence, Sentinel `Hermes Axiom Sync` cron state, and optional read-only Axiom-Desktop SSH branch probing.
- Updated `FORK.md` to point at `docs/axiom-fork-contract.md` for the concise branch/Desktop contract and to treat old generated counts as historical, not live status.
- Updated `docs/axiom-fork-contract.md` with a source-of-truth table and status-helper workflow.
- Updated Obsidian runbook/overview/cron roster plus `~/SYSTEM.md` to reflect that Sentinel `Hermes Axiom Sync` remains paused while Anthropic/system-prompt conflicts and validation coverage are hardened.

### Verification

- `python -m py_compile scripts/fork-status.py` → OK.
- `scripts/fork-status.py` → produced local read-only report.
- `scripts/fork-status.py --desktop` → failed safely with `ssh: connect to host axiom-desktop port 22: Connection timed out`.

## 2026-06-17 — Carry Claude OAuth billing-lane candidate stack

### Summary

Built and validated an isolated Axiom merge-candidate stack for Anthropic/Claude Code OAuth subscription billing-lane safety, then prepared it for promotion without touching the dirty live `axiom` checkout.

### What changed

- Cherry-picked upstream #47723 onto the Axiom candidate branch: OAuth wire tool names are encoded as `mcp__*` instead of single-underscore `mcp_*`, and response names dispatch back to registered tool names.
- Cherry-picked upstream #23361: concrete `tool_choice` names use the same OAuth wire-name encoding as the `tools` array.
- Cherry-picked upstream #47738: large Hermes system prompts are relocated out of Anthropic `system[]` into a cache-marked first-user `<system_context>` preamble on the OAuth path.
- Added an Axiom conflict-resolution fix preserving the double-underscore response-strip prefix in `agent/transports/anthropic.py` after the #47723/#47738 stack was applied.
- Kept the existing `claudetest` profile isolated and fallback-free for billing-lane smoke tests.
- Updated fork refs and provider docs (`FORK.md`, English provider docs, zh-Hans provider docs) so Anthropic OAuth guidance no longer says Claude Max extra-usage credits are required for the Claude Code subscription lane.

### Verification

- `python -m py_compile agent/anthropic_adapter.py agent/transports/anthropic.py` → OK.
- `python -m pytest tests/agent/test_anthropic_mcp_prefix_strip.py tests/agent/test_anthropic_adapter.py tests/agent/test_anthropic_oauth_system_relocation.py -q -o 'addopts='` → 186 passed.
- Live `claudetest` Anthropic OAuth smoke with `fallback_providers: []` and the candidate worktree returned exactly `CLAUDE_OAUTH_CANDIDATE_OK` in session `20260617_082107_a154c8` after a real `read_file` tool call.
- After updating the live checkout and popping local WIP, the same targeted tests passed from `~/.hermes/hermes-agent` (`186 passed in 11.41s`), and live `claudetest` OAuth smoke returned exactly `CLAUDE_OAUTH_LIVE_UPDATED_OK` in session `20260617_104145_01d5d6`.
- Broader `python -m pytest tests/agent -q -o 'addopts='` did not complete: first failure was pre-existing/unrelated auxiliary routing behavior (`tests/agent/test_auxiliary_client.py::TestExpiredCodexFallback::test_expired_codex_openrouter_key_is_ignored_for_aux_auto`), then the run timed out at 600s. Targeted Anthropic OAuth coverage passed.

## 2026-06-16 — Fix Desktop model picker snap-back

### Summary

Fixed a Desktop composer/model-picker state race where an explicit model selection could visually flicker back to stale `model.options` metadata or route through a stale active-session prop, causing new test chats to keep launching on the wrong provider/model.

### What changed

- Made Desktop model picker/menu rendering prefer the live composer/session model stores over lagging `model.options` query metadata.
- Made `useModelControls` resolve the live runtime session from `$activeSessionId` with the hook prop as fallback, so picker changes route through the actual active session even during a brief render/state mismatch.
- Added a regression for stale hook prop vs live active-session atom behavior.

### Verification

- `NODE_ENV=test npm run test:ui -- src/app/session/hooks/use-model-controls.test.tsx` in `apps/desktop` → 6 passed.
- `NODE_ENV=test npm run typecheck` in `apps/desktop` → OK.
- `NODE_ENV=test npx eslint src/app/session/hooks/use-model-controls.ts src/app/session/hooks/use-model-controls.test.tsx src/app/shell/model-menu-panel.tsx src/components/model-picker.tsx` in `apps/desktop` → OK.
- Full `npm run lint` still fails on pre-existing unrelated Desktop lint errors outside this patch.

## 2026-06-16 — Import upstream remote artifact download fixes

### Summary

Confirmed upstream merged the remote artifact preview/download fix, then brought the relevant upstream commits onto `axiom` ahead of the next full upstream sync.

### What changed

- Cherry-picked upstream PR #47011 with `-x`: global remote/Docker Desktop mode now forwards profile-scoped REST calls with the active profile and scopes OAuth status/start/completion paths to that profile.
- Cherry-picked upstream PR #46895 with `-x`: remote artifacts/generated files now open through authenticated `/api/files/download?path=…&token=…` instead of gateway-host `file://` paths on the Windows client.
- Resolved the `hermes_cli/web_server.py` auth conflict by keeping Axiom's broader `_has_valid_token()` behavior (`X-Hermes-Session-Token`, session Bearer, and `HERMES_GATEWAY_TOKEN`) while adding upstream's narrowly-scoped query-token allowance for `/api/files/download` only.
- Updated `docs/axiom-fork-contract.md` to mark #46895/#47011 as upstream-merged remote file/access behavior and clarify that Axiom's #44538 carry is now only the chat fallback/error-state wrapper.

### Verification

- `python -m py_compile hermes_cli/web_server.py hermes_cli/auth.py` → OK.
- `python -m pytest -q -o addopts='' tests/hermes_cli/test_web_server_files.py tests/hermes_cli/test_web_server_fs.py tests/hermes_cli/test_nous_auth_status_cache.py tests/hermes_cli/test_web_oauth_dispatch.py` → 48 passed.
- `NODE_ENV=test npm run test:ui -- src/lib/media.remote.test.ts src/lib/media.test.ts src/lib/desktop-fs.test.ts` → 18 passed.
- `npm run typecheck` in `apps/desktop` → OK.
- `node --test electron/connection-config.test.cjs` in `apps/desktop` → 50 passed.

## 2026-06-15 — Carry remote Desktop chat file downloads

### Summary

Verified the post-update Axiom branch includes upstream's merged remote Desktop file-browser fixes, then carried the remaining clean upstream PR for remote-mode chat/media file downloads while keeping the patch documented for upstream replacement.

### What changed

- Confirmed `axiom` contains upstream remote Desktop file access fixes: #44326 remote `/api/fs/*` browsing, #43109 remote file drop staging, #46658 profile-switch `$connection` sync, and #45057 active-profile new chat behavior.
- Cherry-picked upstream PR #44538 with authorship preserved and `-x`: remote-mode chat/media fallback links now fetch bytes through the authenticated `/api/fs/read-data-url` bridge instead of opening gateway-host `file://` paths on Windows.
- Updated `docs/axiom-fork-contract.md` to separate upstream-merged remote file access behavior from the current `axiom` carry and to mark broader remote SSH/workspace editing PRs as deliberately not carried.

### Verification

- `NODE_ENV=test npm run test:ui -- src/lib/media.test.ts` → 4 passed.
- `npm run typecheck` in `apps/desktop` → OK.
- `python -m py_compile hermes_cli/web_server.py` → OK.
- `python -m pytest -q -o addopts='' tests/hermes_cli/test_web_server_fs.py` → 13 passed.
- `NODE_ENV=test npm run test:ui -- src/lib/desktop-fs.test.ts src/lib/media.test.ts` → 8 passed.

## 2026-06-08 — Sync Axiom fork with upstream and retire Discord multi-agent orchestration

### Summary

Merged current `upstream/main` into the Axiom integration branch in a non-live worktree before touching the deployed checkout, retired the unused Discord multi-agent orchestration feature surface, and strengthened Hermes-Relay/API compatibility regression coverage.

### What changed

- Resolved upstream merge conflicts in `/home/bailey/.hermes/worktrees/hermes-axiom-upstream-sync-20260608` on branch `chore/axiom-upstream-sync-20260608`; the live deploy checkout was not modified.
- Removed the Discord multi-agent orchestration plugin, slash-command docs, env/config references, tests, and fork-contract protected-surface entries at operator direction.
- Kept generic Discord safety behavior that still matters without the removed feature: explicit bot admission through `allow_bots`, `thread_require_mention`, safe allowed mentions, and reply-ping suppression for bot-authored Discord turns.
- Removed duplicate `APIServerAdapter` handler definitions left by the merge so upstream/native session handlers cannot be silently shadowed.
- Expanded API-server regression tests to require all Hermes-Relay compatibility routes, enforce no duplicate adapter handler methods, verify compatibility-route auth, and assert `/api/sessions/search` response shape.

### Verification

- `python3 -m pytest -q tests/gateway/test_api_server.py tests/gateway/test_discord_channel_controls.py tests/gateway/test_discord_send.py tests/test_tui_gateway_server.py tests/hermes_cli/test_update_check.py tests/hermes_cli/test_update_autostash.py` → 489 passed, 115 aiohttp AppKey warnings.
- `python3 -m pytest -q tests/gateway/test_session_api.py tests/gateway/test_webhook_adapter.py tests/hermes_cli/test_webhook_cli.py tests/gateway/test_reasoning_command.py tests/gateway/test_discord_allowed_mentions.py tests/hermes_cli/test_proxy.py tests/agent/test_anthropic_adapter.py tests/hermes_cli/test_plugins.py tests/hermes_cli/test_subcommands_batch.py tests/hermes_cli/test_subcommands_followup.py` → 471 passed.
- `python3 -m py_compile hermes_cli/main.py hermes_cli/config.py hermes_cli/subcommands/update.py gateway/run.py gateway/config.py gateway/platforms/api_server.py gateway/platforms/base.py plugins/platforms/discord/adapter.py agent/anthropic_adapter.py tui_gateway/server.py tests/gateway/test_api_server.py tests/gateway/test_discord_channel_controls.py tests/gateway/test_discord_send.py` → OK.
- Legacy feature-name grep → no matches.
- `git diff --cached --check` → OK after whitespace cleanup on merged upstream files.

## 2026-06-08 — Create Axiom fork contract and pause daily sync

### Summary

Paused the Sentinel `Hermes Axiom Sync` cron job while the fork is hardened, then created `FORK.md` as the canonical contract for Axiom-specific Hermes behavior.

### What changed

- Paused cron job `44f7334c4efc` (`Hermes Axiom Sync`) so the daily upstream merge attempt stops paging on known unresolved fork conflicts.
- Added `FORK.md` with the protected behavior contract for Hermes-Relay/API compatibility, Forge, Discord multi-agent orchestration safety, webhook route-level toolsets, proxy/provider routing, update/deploy behavior, TUI/plugin-command cards, and local memory/Lucid surfaces.
- Inventoried the current fork-only commit surface: 95 non-merge fork-only commits, 107 changed files from the fork merge-base to `origin/axiom`, and major hotspots in `hermes_cli/main.py`, `gateway/platforms/api_server.py`, `gateway/run.py`, and `tui_gateway/server.py`.
- Recorded required validation commands and open questions before resuming automated upstream sync.

### Verification

- `cronjob(action="list")` confirmed `44f7334c4efc` is paused.
- `git cat-file -e origin/axiom:FORK.md` and `git cat-file -e upstream/main:FORK.md` confirmed neither remote branch already had `FORK.md`.
- `git cherry -v upstream/main origin/axiom` showed 95 non-merge fork commits as not patch-equivalent to upstream.

## 2026-06-06 — Enforce Forge per-run host tool policy

### Summary

Added Hermes host-side enforcement for Forge `/v1/runs` `tool_allowlist` /
`runtime_policy` payloads without disabling the rest of the Hermes agent
surface.

### What changed

- Added runtime tool-policy helpers that translate Forge host tools
  (`terminal`, `filesystem`, `git`) into Hermes disabled toolsets.
- `/v1/runs` now validates/stores engagement mode, contract version, runtime
  policy, and host tool policy, then passes the derived disabled toolsets into
  `AIAgent`.
- `handle_function_call()` now blocks disabled toolsets before registry
  dispatch, so denied tools cannot run even if a model emits an old schema.
- Restricted Forge runs deny local terminal/file/code/desktop surfaces but keep
  Hermes skills, memory, web/search, Forge context tools, and delegation
  available.
- Delegated subagents inherit the parent's disabled toolsets, so a Review or
  Research run can still use Hermes orchestration without regaining local repo
  access.

### Verification

- `venv/bin/python -m pytest tests/agent/test_runtime_tool_policy.py tests/test_model_tools.py::TestHandleFunctionCall::test_disabled_toolset_blocks_before_dispatch tests/gateway/test_api_server_toolset.py::TestApiServerAdapterToolset::test_create_agent_merges_run_disabled_toolsets tests/gateway/test_api_server_runs.py::TestStartRun::test_start_applies_host_tool_allowlist` → 9 passed.
- `venv/bin/python -m pytest tests/tools/test_delegate.py -k inherits_disabled_toolsets` → 1 passed.

## 2026-06-05 — Remove duplicate API session route registration

### Summary

Cleaned up the `axiom` API-server patch layer after upstream landed the baseline session-control surface in `f7527b0fdb54f01691547df03fc65a6d367f9fde`.

### What changed

- Removed the duplicate legacy `/api/sessions/*` route registration block from `APIServerAdapter.connect()`.
- Preserved Axiom/Relay-only compatibility routes: `/api/sessions/search`, `/api/memory`, `/api/skills`, `/api/config`, and `/api/available-models`.
- Moved the Hermes-Relay bootstrap feature-detection hook until after native fork routes are registered so bootstrap shims no-op instead of shadowing first-class handlers.
- Added a route-registration regression test that fails if the adapter registers the same method/path more than once.
- Merged fork PR #3 into `origin/axiom`.

### Verification

- RED check before implementation: `python -m pytest -q -o addopts='' tests/gateway/test_api_server.py::test_connect_registers_each_route_once` → failed with 9 duplicate route registrations.
- `python -m py_compile gateway/platforms/api_server.py` → OK.
- `python -m pytest -q -o addopts='' tests/gateway/test_api_server.py tests/gateway/test_session_api.py --tb=short` → 175 passed, 112 existing aiohttp AppKey warnings.
- GitHub PR checks for #3 → all passed (`changes`, dependency bounds, supply-chain scan, nix macOS, nix Ubuntu).
- Post-merge focused rerun: `python -m pytest -q -o addopts='' tests/gateway/test_api_server.py::test_connect_registers_each_route_once tests/gateway/test_session_api.py --tb=short` → 11 passed.
- Route parse check on merged `axiom`: 49 route registrations, 49 unique method/path pairs, no duplicates.

## 2026-06-03 — Sync upstream into axiom deploy branch

### Summary

Merged the pending upstream/main changes into the `axiom` deploy branch after `hermes update` stopped on conflicts, then reran the update path so the live checkout fast-forwarded and active Hermes services refreshed.

### What changed

- Resolved `hermes_cli/main.py` conflicts by keeping upstream TUI freshness/workspace install handling and combining dashboard PID exclusions for both systemd-managed dashboard restarts and Desktop child backends.
- Resolved `tests/hermes_cli/test_update_stale_dashboard.py` by preserving coverage for systemd dashboard PID exclusion and upstream Desktop child PID exclusion.
- Pushed the resolved deploy branch to `origin/axiom` and reran `hermes update`; the rerun pulled two additional upstream commits and completed dependency refresh, web UI build, skill sync, config migration, and service restarts.
- Cleared the stale `.update_handoff.json` marker after confirming live `HEAD == origin/axiom` and `upstream/main` is included in the deploy branch.
- Restarted `hermes-dashboard.service` and `hermes-proxy.service` manually after verifying their process start times predated the update restart wave.

### Verification

- `python -m py_compile gateway/platforms/api_server.py gateway/run.py hermes_cli/main.py hermes_cli/web_server.py tui_gateway/server.py tests/hermes_cli/test_update_stale_dashboard.py` → OK.
- `python -m pytest -q -o addopts='' tests/hermes_cli/test_update_stale_dashboard.py tests/hermes_cli/test_update_concurrent_quarantine.py tests/hermes_cli/test_update_autostash.py tests/hermes_cli/test_cmd_update.py tests/hermes_cli/test_update_check.py` → 107 passed.
- `python -m pytest -q -o addopts='' tests/gateway/test_api_server.py tests/hermes_cli/test_proxy.py tests/hermes_cli/test_web_server.py` → 423 passed.
- Post-update focused rerun: `python -m pytest -q -o addopts='' tests/hermes_cli/test_update_stale_dashboard.py` → 22 passed.
- `hermes --version` → Hermes Agent v0.15.1 (2026.5.29), branch `axiom`, up to date.
- Live smoke: API `/health` OK, dashboard GET on `172.16.24.250:9119` returned HTML, proxy `/v1/models` returned 4 models, all active Hermes gateway/profile/dashboard/proxy units reported `active`.

## 2026-05-28 — Add route-level webhook toolsets

### Summary

Restored trusted GitHub Actions remediation webhooks without undoing the safe default webhook hardening. Webhook routes can now specify their own `toolsets` list, so public/generic webhooks stay constrained while signed internal automation can get the code-capable tools it needs.

### What changed

- Added `MessageEvent.enabled_toolsets` as a per-message toolset override.
- Webhook routes now accept `toolsets` as a list or comma-separated string and attach the cleaned value to the generated `MessageEvent`.
- Gateway agent creation resolves route-level webhook toolsets through the same toolset resolver used by `platform_toolsets`, preserving MCP/default semantics unless `no_mcp` is specified.
- Added `hermes webhook subscribe --toolsets ...` and list/create display output.
- Updated the live `orca-merge-remediation` subscription with `web`, `terminal`, `file`, `code_execution`, `skills`, and `session_search` while leaving generic webhook defaults untouched.
- Updated webhook docs, bundled/local skill references, SYSTEM.md, and Obsidian Hermes/Forge notes.

### Verification

- `python -m pytest tests/gateway/test_webhook_adapter.py::TestRouteToolsets tests/gateway/test_webhook_adapter.py::TestHTTPHandling::test_route_toolsets_attached_to_message_event tests/gateway/test_reasoning_command.py::TestReasoningCommand::test_run_agent_accepts_per_event_toolset_override tests/hermes_cli/test_webhook_cli.py::TestSubscribe::test_with_options -q -o 'addopts='` → 6 passed.

## 2026-05-20 — Improve Discord multi-agent orchestration call UX

### Summary

Tightened `/Discord multi-agent orchestration call` so it actually admits the target agent's one reply while keeping the shared circuit breaker closed, and improved Discord multi-agent orchestration usability/output.

### What changed

- `/Discord multi-agent orchestration call` now writes a short-lived `pending_call` token after the controlled mention. The stopped gate admits exactly one matching bot-authored event from the caller bot, in the expected channel/thread, mentioning the target bot, then consumes the token.
- `/Discord multi-agent orchestration stop` cancels any pending call along with active debates.
- Consensus/max-turn debate notices now post a cleaner result block with topic, turn count, final/last turn text, and the decision marker stripped.
- Discord native `/Discord multi-agent orchestration` now has richer slash input: action choices plus separate `agent`, `participants`, `rounds`, and `message` fields instead of only one opaque args field.
- Debate defaults now allow a little more room: omitted `rounds` defaults to 3, and explicit rounds are capped at 5.
- Updated Discord multi-agent orchestration plugin tests and the reusable Hermes skill reference.

### Verification

- `python -m py_compile gateway/platforms/discord.py plugins/Discord multi-agent orchestration_orchestrator/__init__.py` → OK
- `python -m pytest tests/gateway/test_discord_Discord multi-agent orchestration.py tests/gateway/test_discord_allowed_mentions.py tests/plugins/test_Discord multi-agent orchestration_orchestrator_plugin.py -q -o 'addopts='` → 56 passed.
- Hermetic Discord sweep with temp `HERMES_HOME`: `python -m pytest tests/gateway/test_discord*.py tests/plugins/test_Discord multi-agent orchestration_orchestrator_plugin.py -q -o 'addopts='` → 395 passed.

## 2026-05-20 — Stop Discord multi-agent orchestration consensus loops

### Summary

Fixed a live Discord multi-agent orchestration loop where consensus replies could keep re-triggering Victor/Mizu after debate completion.

### What changed

- Debate consensus and max-turn stops now fail closed by setting the shared Discord multi-agent orchestration gate back to disabled with explicit stop reasons.
- Discord responses to bot-authored Discord multi-agent orchestration turns now suppress reply-author pings so `allow_bots: mentions` does not treat reply metadata as a fresh bot mention.
- Added regression coverage for fail-closed debate completion and reply-mention suppression.

### Verification

- `python -m py_compile plugins/Discord multi-agent orchestration_orchestrator/__init__.py gateway/platforms/base.py gateway/platforms/discord.py gateway/run.py tests/plugins/test_Discord multi-agent orchestration_orchestrator_plugin.py tests/gateway/test_discord_allowed_mentions.py tests/gateway/test_Discord multi-agent orchestration_orchestrator_dispatch.py tests/gateway/test_discord_Discord multi-agent orchestration.py` → OK
- `python -m pytest tests/plugins/test_Discord multi-agent orchestration_orchestrator_plugin.py tests/gateway/test_discord_Discord multi-agent orchestration.py tests/gateway/test_Discord multi-agent orchestration_orchestrator_dispatch.py tests/hermes_cli/test_plugins.py tests/gateway/test_discord_allowed_mentions.py -q -o 'addopts='` → 142 passed.
- `python -m pytest tests/gateway/test_telegram_thread_fallback.py tests/gateway/test_background_command.py tests/gateway/test_send_voice_reply_notify.py -q -o 'addopts='` → 64 passed.

## 2026-05-20 — Add bounded Discord multi-agent orchestration debate mode

### Summary

Added `/Discord multi-agent orchestration debate` as a supervised turn scheduler for Victor/Mizu/Sentinel-style Discord rooms. Normal Discord multi-agent orchestration remains safe shared-room behavior; debate mode is explicit, bounded, and auto-stops with a summary on consensus or max turns.

### What changed

- Added `/Discord multi-agent orchestration debate <agent1,agent2[,agent3]> [--rounds N] <topic>` to the `Discord multi-agent orchestration-orchestrator` plugin.
- Persisted active debate state in `~/.hermes/Discord multi-agent orchestration_state.json` alongside the existing circuit breaker.
- Added controlled per-turn Discord mentions, with only the next expected participant allowed in `allowed_mentions.users`.
- Added `post_gateway_send` plugin hook support in the Discord adapter so debate state advances after the expected bot posts its response.
- Added consensus auto-stop: expected participants can end with `ROUND_TABLE_DECISION: CONSENSUS`; the orchestrator then posts a non-mention summary and marks the debate stopped.
- Added max-turn auto-stop for bounded rounds when consensus is not reached.
- Updated docs/skill/Obsidian references for call vs debate vs normal safe-room behavior.

### Verification

- `python -m py_compile plugins/Discord multi-agent orchestration_orchestrator/__init__.py gateway/platforms/discord.py hermes_cli/plugins.py tests/plugins/test_Discord multi-agent orchestration_orchestrator_plugin.py tests/gateway/test_discord_Discord multi-agent orchestration.py tests/gateway/test_Discord multi-agent orchestration_orchestrator_dispatch.py` → OK
- `python -m pytest tests/plugins/test_Discord multi-agent orchestration_orchestrator_plugin.py tests/gateway/test_discord_Discord multi-agent orchestration.py tests/gateway/test_Discord multi-agent orchestration_orchestrator_dispatch.py tests/hermes_cli/test_plugins.py -q -o 'addopts='` → 119 passed.

## 2026-05-20 — Add single-fire Discord multi-agent orchestration calls

### Summary

Extended the bundled `Discord multi-agent orchestration-orchestrator` plugin with an explicit `/Discord multi-agent orchestration call`/`summon` primitive so a human-facilitated agent can pull exactly one other Discord bot into a shared Discord multi-agent orchestration without relaxing the default outbound mention guard.

### What changed

- Added `/Discord multi-agent orchestration call <agent> <message>` and aliases `summon`/`page` backed by `discord.Discord multi-agent orchestration.agents` or `HERMES_Discord multi-agent orchestration_AGENTS`/`DISCORD_Discord multi-agent orchestration_AGENTS` name-to-bot-ID mappings.
- Passed gateway command context (`gateway`, `event`) into plugin command handlers with backwards-compatible fallback for older handlers.
- Added a Discord send metadata escape hatch for controlled one-shot bot mentions using precise `allowed_mentions.users`, while keeping normal Discord multi-agent orchestration replies escaped by default.
- Updated plugin/gateway/Discord adapter regression coverage for the single-fire call path and rejected non-Discord multi-agent orchestration channels.

### Verification

- `python -m py_compile plugins/Discord multi-agent orchestration_orchestrator/__init__.py gateway/run.py gateway/platforms/discord.py` → OK
- `python -m pytest tests/plugins/test_Discord multi-agent orchestration_orchestrator_plugin.py tests/gateway/test_Discord multi-agent orchestration_orchestrator_dispatch.py tests/gateway/test_discord_Discord multi-agent orchestration.py -q -o 'addopts='` → 33 passed.

## 2026-05-20 — Add Discord multi-agent orchestration circuit breaker plugin

### Summary

Added a bundled `Discord multi-agent orchestration-orchestrator` plugin as a runtime brake for Discord multi-agent rooms. The plugin does not replace Discord bot-admission policy or create a new orchestration path; it adds `/Discord multi-agent orchestration status|stop|start` and a shared fail-closed pre-dispatch gate for admitted Discord bot-authored events.

### What changed

- Added `plugins/Discord multi-agent orchestration_orchestrator/` with a gateway-only `/Discord multi-agent orchestration` command.
- Added shared state at `~/.hermes/Discord multi-agent orchestration_state.json`, overrideable via `HERMES_Discord multi-agent orchestration_STATE`.
- Added optional Discord multi-agent orchestration channel scoping via `HERMES_Discord multi-agent orchestration_CHANNELS` / `DISCORD_Discord multi-agent orchestration_CHANNELS` or `discord.Discord multi-agent orchestration.channels`.
- Added `pre_gateway_dispatch` coverage so stopped Discord multi-agent orchestrations skip admitted Discord bot turns before any LLM call.
- Updated focused gateway/plugin tests and isolated Discord multi-agent orchestration env vars in Discord multi-agent orchestration tests.

### Verification

- `python -m py_compile plugins/Discord multi-agent orchestration_orchestrator/__init__.py gateway/run.py gateway/platforms/discord.py` → OK
- `python -m pytest tests/gateway/test_Discord multi-agent orchestration_orchestrator_dispatch.py -q -o 'addopts='` → 5 passed.
- `python -m pytest tests/plugins/test_Discord multi-agent orchestration_orchestrator_plugin.py tests/gateway/test_Discord multi-agent orchestration_orchestrator_dispatch.py tests/gateway/test_discord_Discord multi-agent orchestration.py -q -o 'addopts='` → 29 passed.

## 2026-05-20 — Add Discord multi-agent orchestration safety controls

### Summary

Added a focused Discord gateway patch for human-facilitated multi-profile rooms. The patch keeps solo-bot behavior unchanged by default, adds config parity for bot-authored message admission, and introduces opt-in Discord multi-agent orchestration safeguards so Victor/Mizu/Sentinel-style profiles can see each other's context without accidental bot-to-bot cascades.

### What changed

- Added `discord.allow_bots` config support matching `DISCORD_ALLOW_BOTS=none|mentions|all`.
- Added `discord.Discord multi-agent orchestration` controls for safe multi-agent rooms: `enabled`, `include_bot_history`, `outbound_bot_mentions`, and `participant_bot_ids`.
- Refactored Discord bot-message admission into tested adapter helpers.
- Made history backfill use the normalized bot-history policy instead of env-only checks.
- Escapes configured participant bot mentions in outbound Discord replies when Discord multi-agent orchestration mode is enabled, preventing accidental live pings to other Hermes bots.
- Documented the human-facilitated Discord multi-agent orchestration pattern in the Discord messaging docs and Hermes skill reference.

### Verification

- `python -m py_compile gateway/platforms/discord.py gateway/config.py hermes_cli/config.py tests/gateway/test_discord_Discord multi-agent orchestration.py tests/gateway/test_discord_send.py` → OK
- `python -m pytest tests/gateway/test_discord_Discord multi-agent orchestration.py tests/gateway/test_discord_bot_filter.py tests/gateway/test_discord_send.py -q -o 'addopts='` → 42 passed.
- `python -m pytest tests/gateway/test_discord_thread_persistence.py tests/gateway/test_discord_allowed_channels.py tests/gateway/test_discord_allowed_mentions.py tests/gateway/test_discord_free_response.py tests/gateway/test_discord_slash_commands.py -q -o 'addopts='` → 109 passed.
- `python -m pytest tests/gateway/test_config.py tests/gateway/test_config_env_bridge_authority.py tests/gateway/test_runtime_env_reload_config_authority.py -q -o 'addopts='` → 52 passed, 1 order-dependent failure in pre-existing `test_bridges_quoted_false_platform_enabled_from_config_yaml`; the same test passes in isolation.

## 2026-05-19 — Advertise current Codex models through Hermes Proxy router

### Summary

ModelFoundry discovery omitted `gpt-5.5` because the local `hermes proxy --provider auto` routed adapter served a hardcoded synthetic `/v1/models` list that still only advertised `gpt-5.4`, `gpt-5.4-mini`, and `gpt-5.3-codex` for the Codex lane.

### What changed

- Updated `hermes_cli/proxy/adapters/routed.py` so the routed adapter derives OpenAI Codex entries from `DEFAULT_CODEX_MODELS` plus the existing forward-compat synthesis helper instead of maintaining a stale duplicate static list.
- Extended the routed adapter toward auth-driven discovery: `/v1/models` now builds from authenticated proxy adapters (`xai-oauth`, `openai-codex`, `nous` when logged in) and their Hermes model catalogs, with a static fallback only for auth churn/tests.
- Filtered non-text media model IDs out of the routed proxy catalog so chat-oriented downstream routers do not pick image/video models.
- Added regression coverage for `gpt-5.5` advertisement and slash-prefixed Nous model routing.

### Verification

- `python -m pytest tests/hermes_cli/test_proxy.py -q -o addopts=''` → 32 passed.
- Restarted `hermes-proxy.service`.
- `GET http://172.16.24.250:8648/v1/models` now returns 17 text/chat models from authenticated proxy adapters, including `gpt-5.5`.

## 2026-05-19 — Shelve local Model Router plugin references

### Summary

Shelved the local Model Router path without restarting Hermes services. The live plugin directories were moved outside plugin discovery, profile configs now keep `model-router` disabled, and source comments no longer point future plugin work at the archived live path.

### What changed

- Archived `model-router` and `hermes-admin` out of `~/.hermes/plugins/` into `~/.hermes/plugin-archive/shelved-20260519_155645/`.
- Updated profile configs so `model-router` is absent from every `plugins.enabled` list and present in `plugins.disabled`.
- Updated comments in `hermes_cli/commands.py` and `gateway/run.py` to describe `/route` as a shelved legacy plugin command example, not a live plugin path.
- Updated SYSTEM/Obsidian/Hermes skill docs separately to mark the lane as shelved.

### Verification

- Parsed root/profile config YAML and asserted no live profile enables `model-router`.
- Checked live plugin discovery roots: no `model-router` or `hermes-admin` directories remain under `~/.hermes/plugins` or profile plugin roots.
- No gateway/dashboard restart performed by request; loaded runtime copies will unload naturally on next restart/reboot.

## 2026-05-18 — Restore Forge chat delivery adapter

### Summary

Investigated Forge chat runs that showed a thinking indicator without a final reply. Hermes accepted the Forge webhook and generated a response, but the webhook delivery layer failed with `Unknown deliver type: forge` because the outbound Forge platform adapter was missing from the current gateway runtime.

### What changed

- Added `plugins/platforms/forge/` platform plugin.
- Implemented `ForgeAdapter` for outbound webhook delivery into Forge chat threads via the Forge MCP `chat.appendMessage` tool.
- Treated `[SILENT]` as successful no-op delivery so echoed AGENT chat events do not create empty/noisy replies.
- Added regression tests for plugin registration, Forge MCP append calls, and silent no-op delivery.

### Verification

- `python -m py_compile plugins/platforms/forge/adapter.py plugins/platforms/forge/__init__.py` → OK
- `pytest tests/gateway/test_forge_plugin.py tests/gateway/test_webhook_adapter.py -q -o addopts=''` → 58 passed
- Live signed synthetic POST to `/webhooks/forge-dispatch` delivered `forge-e2e-smoke-*` back into Forge thread `cmohwivz30005mo076bmti9y5`.

## 2026-04-23 — Merge `main` into `axiom` after upstream sync (14 commits)

### Summary
Resolved the failed `hermes update` merge manually after `main` advanced to `ce089169` from upstream and `axiom` still carried the local router/plugin/TUI fork surface. Conflicts landed in three expected hotspots: `agent/anthropic_adapter.py`, `hermes_cli/plugins.py`, and `tui_gateway/server.py`. Resolution strategy was "take upstream improvements, keep current axiom behavior" — especially for Model Router and TUI plugin-command handling.

### Conflict resolution
- `agent/anthropic_adapter.py`
  - kept our mutation-aware thinking-signature stripping for direct Anthropic
  - kept upstream's newer Kimi `/coding` special-case branch
  - combined them so Kimi handling wins first, then the third-party/non-latest/mutated stripping logic applies everywhere else
- `hermes_cli/plugins.py`
  - kept the richer axiom plugin command surface (`subcommands`, `category`, `gateway_only`, `cli_only`, `aliases`, `returns_card`)
  - kept synthetic `CommandDef` registration into the global command registry
  - absorbed upstream's `args_hint` normalization/adapter-facing behavior into the merged docstring + stored value
- `tui_gateway/server.py`
  - kept the newer first-class plugin command path via `get_plugin_command_entry(...)`
  - kept TUI card rendering + `session_id` passing
  - kept the legacy hook fallback for compatibility
  - dropped the stale upstream bare-handler branch that would have regressed `/route status` back to raw dict output in the TUI

### Verification
- `python -m py_compile agent/anthropic_adapter.py hermes_cli/plugins.py tui_gateway/server.py run_agent.py gateway/run.py hermes_cli/commands.py` → OK
- `pytest -q tests/agent/test_anthropic_adapter.py tests/hermes_cli/test_plugins.py tests/tui_gateway/test_protocol.py -o addopts=''` → 244 passed

### Result
- `main` merged into `axiom`
- upstream changes from the 14-commit sync are present
- current Model Router / plugin-command / TUI card behavior preserved

## 2026-04-23 — TUI plugin-command alignment for Model Router cards

### Summary
Audited the remaining router/plugin-command fork surface against upstream and found one real mismatch worth fixing immediately: the TUI still handled plugin commands via the older bare-handler path, so first-class plugin commands with `returns_card=True` (like Model Router's `/route status`) rendered as raw Python dicts instead of readable text. Updated the TUI to use full plugin command entries, pass `session_id`, render InfoCards through the shared card renderer, and redirect gateway-only plugin commands out of the slash worker and into `command.dispatch`.

### What changed
- `tui_gateway/server.py`
  - `command.dispatch` now uses `get_plugin_command_entry(...)` instead of the legacy bare-handler lookup
  - passes `session_id` to plugin handlers when available
  - renders InfoCard dicts via `gateway.cards.render_card_as_text(...)`
  - `slash.exec` now redirects gateway-only plugin commands registered through the plugin command surface (not just old hook-based handlers)
- `tests/tui_gateway/test_protocol.py`
  - added regression test for gateway-only plugin command redirect in `slash.exec`
  - added regression test ensuring `command.dispatch` renders plugin cards cleanly and passes `session_id`

### Verification
- `pytest -q tests/tui_gateway/test_protocol.py tests/hermes_cli/test_plugins.py -o addopts=''` → 103 passed
- Live smoke:
  - `command.dispatch route status` now renders clean markdown text instead of a raw dict
  - `slash.exec route status` now correctly returns 4018 so the TUI falls through to `command.dispatch`

### Audit result
- **Keep:** `resolve_model` hook in `run_agent.py` — still the core seam Model Router needs; upstream does not have it
- **Keep:** plugin command metadata/card support in `hermes_cli/plugins.py` + `hermes_cli/commands.py` — still required for first-class `/route` UX
- **Keep:** `gateway/cards.py` + Discord/base adapter support — necessary if we want rich `/route status` output without losing functionality
- **Fixed, not removed:** stale TUI plugin-command path; now aligned with the newer first-class plugin command model
- **Not touched:** unrelated `bench` and update-brief plumbing in `gateway/run.py`; outside this router-focused cleanup pass

## 2026-04-23 — Anthropic OAuth prompt-shim cleanup; keep Model Router intact

### Summary
Reviewed Obsidian + local fork state, isolated the Anthropic-only workaround from the older model-router seam, and removed the three recent Claude Max OAuth prompt-shim commits. Goal was to reduce fork surface and stop carrying custom system-prompt/billing-header logic while preserving the Model Router plugin and its gateway/TUI plumbing.

### What changed
- Reverted `6e3d2d2f` (`feat(anthropic): Claude Max OAuth prompt shim + /auth command`)
- Reverted `c0f6da85` (`fix(anthropic-oauth): cap <system-reminder> injection at 12k chars`)
- Reverted `c6fa0ff7` (`fix(anthropic-oauth): compute billing header after system-reminder prepend`)
- Removed untracked shim leftovers:
  - `scripts/test_anthropic_oauth_smoke.py`
  - `tests/agent/test_auxiliary_client_anthropic_oauth.py`
  - `tests/gateway/test_auth_command.py`
- Left the Model Router path alone (`resolve_model` hook + plugin command/card plumbing)

### Verification
- `python -m py_compile run_agent.py hermes_cli/plugins.py hermes_cli/commands.py gateway/run.py tui_gateway/server.py agent/anthropic_adapter.py agent/auxiliary_client.py cli.py` → OK
- `pytest -q tests/hermes_cli/test_plugins.py tests/hermes_cli/test_commands.py tests/gateway/test_discord_slash_commands.py tests/agent/test_anthropic_adapter.py -o addopts=''` → 322 passed
- Model Router direct smoke with process-local `config._config['enabled']=True`:
  - greeting routed to `claude-haiku-4-5`
  - off-tier `gpt-5.4` caused router stand-down without override
  - `/route status` still returned the expected info card dict
- Note: `~/.hermes/plugins/model-router/config.yaml` is currently `enabled: false`, so the plugin's local pytest file fails if run against live config without overriding that flag

### Docs updated
- Obsidian: `3. System/References/AI Tools/Hermes Agent.md`
- Skills: `hermes-agent`, `hermes-profile-auth`, `hermes-cost-optimization`, `systematic-debugging`
- Guidance now treats Claude Code/Max OAuth as brittle and recommends Anthropic API keys for the reliable path

## 2026-04-21 (evening) — hermes version preview + multi-profile gateway restart

### Summary
Two follow-ups to the morning's update UX work:

1. **`hermes version` preview** — when the repo is behind upstream, print the same categorized digest (summary + top 10 per bucket) the update command emits on completion, so the operator can decide whether updating is worth it right now. Deploy branches compare `main..upstream/main`; others compare `HEAD..origin/main`. Best-effort; opt out with `HERMES_VERSION_NO_PREVIEW=1`.

2. **`hermes gateway restart` multi-profile** — restart now wraps the two git-less phases (restart gateway / check other profiles) in the same `Pipeline` line as update, then enumerates other profiles via `list_profiles()` and offers to restart each one whose gateway is running. TTY prompts `[Y/n]` per profile; `--all-profiles` auto-restarts all; `--no-prompt-profiles` skips the prompt entirely; non-TTY prints an info line + flag hint instead of hanging on input. Child invocations run with `HERMES_GATEWAY_RESTART_NO_RECURSE=1` to prevent recursive prompting.

3. **Env-leak fix on child restart** — `_restart_other_profile` was forwarding the parent's full `os.environ` to the child. Concrete bug that surfaced: the default profile's `API_SERVER_KEY` leaked into the Mizu child process. Mizu's `.env` explicitly has `API_SERVER_ENABLED=false`, but `gateway/config.py` uses `api_server_enabled or api_server_key` — inherited key won, api_server turned on with inherited `API_SERVER_PORT=8642`, collided with Victor's already-bound port. Fix: strip `HERMES_*`/`GATEWAY_*`/`MESSAGING_*` and per-platform prefixes from the child's env. The child's `-p <name>` wrapper sets `HERMES_HOME` fresh, then loads the target profile's `.env` cleanly.

### Factored shared code
Moved the digest renderer out of `write_update_brief` into `_render_digest` + `compute_pending_digest(repo, base, target)` in `update_ui.py`. Both the post-update brief's digest and the version-preview digest are produced from the same renderer — same format, one place to maintain. Verified by comparison: `UpdateBrief.digest` is byte-identical to `compute_pending_digest(...)` with a matching title.

### Commits
- `10506ab0` — feat(version): preview pending upstream commits so you can decide to update
- `b0cdacd7` — feat(gateway): multi-profile restart + pipeline UX
- `7ce0e53d` — fix(gateway/restart): strip profile-leaky env before spawning child

### Related — underlying logic that made the env leak matter
`gateway/config.py` treats api_server as enabled when *either* `API_SERVER_ENABLED=true` *or* `API_SERVER_KEY` is set. That's intentional upstream behavior — people set a key and expect api_server to come up — but it makes profile inheritance unsafe. The env strip is the targeted fix; a defensible upstream PR would be to let an explicit `API_SERVER_ENABLED=false` override a merely-present key. Not patched here to keep the local surface minimal.

---

## 2026-04-21 — Upstream sync (166 commits), pipeline TUI, agent-readable change brief

### Summary
Merged 166 upstream commits into `axiom`. The merge conflicted on `gateway/platforms/api_server.py` — upstream `1010e5fa` hoisted cron-function imports to module scope; our `aea9f5f4` had kept them class-scoped with `staticmethod()` wrappers to fix descriptor binding. Took upstream's approach (all our call sites already referenced module-level names, so the class-level block was redundant). Pushed as `64aa5d93 merge: upstream sync (166 commits)`.

Then reworked the update UX in two follow-up commits (`cb952db6`, `c76f2407`) after the operator flagged that the existing flow gave a stash ref + paths but nothing about *what changed* — neither to the terminal nor back to Discord when `/update` triggered via the gateway.

### What changed
- Resolved `gateway/platforms/api_server.py` conflict: dropped our class-level cron import block in favor of upstream's module-scope version. No behavioral change — all call sites were already `_cron_list(...)` not `self._cron_list(...)`.
- Added `hermes_cli/update_ui.py` with a `Pipeline` class that renders `⠋ fetch upstream | · sync main | · merge → axiom` on one animated line (braille spinner on active phase, `|` separators, `✓`/`✗` glyphs). Falls back to plain per-phase prints on non-TTY.
- Rewired the deploy-branch phases in `_cmd_update_impl` to drive the pipeline instead of three separate prints.
- Added `write_update_brief()` that walks `git log pre_update_head..HEAD`, categorizes commits by conventional-commit prefix, and writes a markdown brief to `~/.hermes/logs/update-briefs/brief-<ts>.md` + mirrors to `~/.hermes/logs/last-update-brief.md`.
- Prints a compact digest (summary line + top 10 commits per category, no file list) inline after the merge so the operator *sees* the change set in the terminal.
- In `--gateway` mode, also writes `~/.hermes/.update_brief_prompt.json`. Added a corresponding pickup in `gateway/run.py::_watch_update_progress`: on exit_code 0 it builds a synthetic `MessageEvent(internal=True)` via `_build_process_event_source({"session_key": session_key})` and calls `adapter.handle_message(...)`, so the agent posts a natural-language summary back in the channel where `/update` was invoked.
- Preloaded `hermes_cli.update_ui` at the top of `_cmd_update_impl` so `_stash_local_changes_if_needed --include-untracked` can't sweep the module before the lazy import finds it — caught this on the first live run with the fresh untracked file.

### Why the conflict pattern keeps recurring
`axiom` carries ~40+ commits concentrated on hot upstream files (esp. `gateway/platforms/api_server.py`). Several are duplicate fixes upstream is independently also solving — this was the second time: the cron descriptor-binding fix (`aea9f5f4`) collided with upstream's refactor, and earlier the `skills_categories` drop (`d4e64290`) collided too. Mitigation: upstream the generic fixes as PRs (they stop being axiom-only hunks), isolate axiom-specific behavior in new files (`webapi/` never conflicts), and sync daily instead of weekly.

### Commits
- `64aa5d93` — merge: upstream sync (166 commits) — conflict resolution in api_server.py
- `651d6a2c` — merge: upstream sync (1 commits) — minor follow-up after first push
- `cb952db6` — feat(update): pipeline TUI + agent-readable changelog brief
- `c76f2407` — feat(update): print brief digest inline + inject agent summary in-channel

### Verification
- `py_compile` on `hermes_cli/main.py`, `hermes_cli/update_ui.py`, `gateway/run.py` → OK
- `hermes update` end-to-end run on axiom → "already up to date" path clean; earlier run with 1 new upstream commit produced a valid brief at `/home/bailey/.hermes/logs/last-update-brief.md`
- Digest rendering verified against the full 150-commit 166-upstream merge range — prints correctly with capped per-category lists

---

## 2026-04-13 — Upstream sync recovery, stash-conflict repair, and update UX fix

### Summary
Ran `hermes update` on the forked deploy branch (`axiom`) after upstream advanced by 60 commits. The upstream merge itself succeeded, but the automatic stash restore hit a conflict in `package-lock.json` and left the user with only a stash ref + manual recovery hint. Recovered the intended local code changes, kept the newer upstream lockfile state, and patched the updater so future stash-restore conflicts print a proper copy/paste block for Victor.

### What changed
- Restored the stashed tracked code changes onto current `axiom` without reintroducing the stale lockfile
- Preserved upstream `package-lock.json` / `package.json` security state instead of replaying the older stashed npm tree wholesale
- Patched `hermes_cli/main.py` so the autostash-restore conflict path now prints a structured rescue block including repo path, branch, stash ref, and conflicted files
- Pushed the repaired `axiom` branch to `origin`

### Files recovered from the stash
- `agent/anthropic_adapter.py`
- `run_agent.py`
- `tests/agent/test_anthropic_adapter.py`
- `tools/browser_providers/browser_use.py`
- `tools/browser_tool.py`
- `tools/tts_tool.py`

### Why `package-lock.json` was not restored verbatim
The stash was created before upstream added a `package.json` override pinning `lodash` to `4.18.1`. Reapplying the older stashed `package-lock.json` on top of the newer upstream dependency graph caused the conflict. The right recovery was to preserve the upstream dependency graph and reapply only the meaningful local code edits.

### Verification
- `pytest tests/agent/test_anthropic_adapter.py -q` → `116 passed`
- `python -m py_compile` on all modified Python files → passed
- `python run_agent.py --help` → passed

### Commits
- `14a83e1f` — `chore(axiom): restore local changes after upstream sync`
- `657b72f7` — `fix(cli): print rescue prompt for stash restore conflicts`

### Notes
- Original stash preserved as a safety net during recovery: `a41238e3c714a0dba9617e6740d948c38c075e76`
- `hermes-gateway` was intentionally not restarted during repair to avoid interrupting active sessions; the operator chose to restart manually afterward
- This repo was missing a root `DEVLOG.md` despite the system convention. Created it in this session

### Open follow-up
- `plugins/memory/mempalace/` exists locally as untracked code and appears to be part of the custom MemPalace integration. Before committing it permanently, verify whether it is fully wired into this fork’s plugin-loading path or still an in-progress local drop-in.

## 2026-04-13 — MemPalace plugin hardening and commit-readiness pass

### Summary
Took the previously untracked MemPalace memory provider from "probably part of our setup" to actual commit-ready code. Root-cause pass found four real problems in the plugin as it existed locally: KG writes were leaking to the default global sqlite path instead of following the provider’s configured palace root, the CLI still used old `wing_*` names and hardcoded local paths, self-reference queries were biased toward `wing_victor`, and pre-compression memory handling mixed half-wired features with unsafe prompt reinjection. Fixed the plumbing, added regression tests, and ran independent review loops until the reviewer stopped finding blocking issues.

### What changed
- Scoped `KnowledgeGraph` to an explicit `kg_path` derived from `palace_path` (or override) instead of relying on MemPalace defaults
- Added `kg_path` support to provider config and unified CLI/runtime path resolution with the provider’s config/env model
- Rewrote `plugins/memory/mempalace/cli.py` to use runtime-resolved paths and `sys.executable` instead of hardcoded server-specific paths
- Switched PARA sync defaults from legacy `wing_inbox` / `wing_system` style names to canonical `inbox` / `system` / `business` / `personal`
- Made CLI sync/mine fail closed on subprocess errors instead of printing fake success
- Fixed `sync --vault ... --prune` so prune uses the same vault root that sync just mined
- Added stable per-profile wing derivation for unknown/custom profiles (`wing_<profile_slug>`), while preserving Victor and Mizu’s existing mappings
- Fixed prefetch keyword matching to avoid false positives like `me` matching `memory`
- Made self-reference queries route to the active profile wing instead of hardcoded `wing_victor`
- Restricted fact extraction so it no longer crosses message boundaries and no longer treats secret-bearing subjects/objects as facts
- Added lightweight secret redaction for obvious credential formats and natural-language forms like `password is ...` before durable writes
- Kept `on_pre_compress()` as a side-effect-only KG extraction hook; stopped treating it as a prompt-note reinjection path
- Added `ContextCompressor.preview_turns_to_summarize()` so the plugin only sees the slice actually being dropped during compression

### Verification
- `pytest tests/plugins/memory/test_mempalace_provider.py tests/agent/test_context_compressor.py tests/agent/test_memory_provider.py tests/hermes_cli/test_plugin_cli_registration.py -q` → `113 passed`
- `python -m py_compile plugins/memory/mempalace/__init__.py plugins/memory/mempalace/cli.py agent/context_compressor.py run_agent.py ...` → passed
- `python -m hermes_cli.main mempalace status` → works against the live store
- Independent reviewer pass → no blocking security or logic issues remaining

### Notes
- Live data still contains older `wing_system` / `wing_personal` drawers from earlier mining. That is historical data drift, not current code behavior. A future re-sync / cleanup pass can normalize the store.
- The MemPalace module docstring still says `Build status: IN PROGRESS`; the code is now good enough to commit, but the banner may deserve cleanup later if we want the docs to stop sounding like a hostage note.
