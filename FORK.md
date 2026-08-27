# TGI Fork Contract

Last reviewed: 2026-08-17
Fork/deploy branch: `tgi`  
Fork remote: `origin` = `https://github.com/Codename-11/hermes-agent.git`  
Upstream remote: `upstream` = `https://github.com/NousResearch/hermes-agent.git`  
Live checkout: `/home/tgi/.hermes/hermes-agent`

## Purpose

The `tgi` branch is a long-lived deployment branch for TGI's live Hermes runtime. It exists only to carry behavior that is required for Atlas/Titan production operations and is not yet available upstream.

This is not a feature branch and not a place for unrelated experiments. The default policy is:

1. Keep upstream behavior whenever it satisfies the same operational requirement.
2. Keep each TGI delta as small, tested, and documented as possible.
3. Remove TGI patches when upstream lands an equivalent or better implementation.
4. Keep live checkout, `origin/tgi`, and this fork ledger in sync after every update handoff.

## Branch contract

- `main` should track `upstream/main` cleanly.
- `tgi` is the deploy branch used by the live Atlas/default runtime.
- `origin/tgi` is the source of truth for the deploy artifact.
- Bare `hermes update` is deploy-branch-aware on `tgi`: it resumes a retained handoff when present, otherwise reconciles `upstream/main` into `origin/tgi` in a temporary worktree before fast-forwarding the live checkout. Reconciliation failures leave the live checkout unchanged.
- `hermes update`, `hermes update --check`, and `hermes --version` are intentionally deploy-branch-aware on `tgi`; operators should not need a special Desktop-only update command.
- Desktop's **client** update UI should use `HEAD..origin/tgi` for installable update availability and `upstream/main...HEAD` only for fork-disparity visibility.
- Desktop's **backend** update UI should treat `HEAD..origin/tgi` as installable update availability. `origin/tgi..upstream/main` is merge-authority disparity only: show it separately and direct an authorized host to the bare deploy-aware `hermes update` flow; do not imply that a normal Desktop/server runtime update will integrate it.
- If upstream has new commits but `origin/tgi` has not moved, Desktop's **client** update UI may show upstream disparity, but it should not present that as an installable Desktop-client update.
- Manual conflict resolution should happen in the retained update worktree, not in the live checkout, unless doing an intentional recovery.
- Never force-pull over `tgi`, flatten `tgi` into `main`, or leave required runtime behavior as uncommitted live checkout changes.

## Update procedure

Normal path:

```bash
cd /home/tgi/.hermes/hermes-agent
git status --short --branch
hermes update --yes
```

Conflict handoff path:

```bash
cd <handoff-worktree>
git status --short --branch
# Resolve conflicts with upstream-first policy.
# Preserve outcomes through tests, not stale hunks.
/home/tgi/.hermes/hermes-agent/venv/bin/python -m py_compile gateway/run.py gateway/platforms/slack.py gateway/session.py hermes_cli/main.py hermes_cli/fork_update.py
/home/tgi/.hermes/hermes-agent/venv/bin/python -m pytest -o 'addopts=' -q <focused-tests>
git commit --no-edit
HOME=/home/tgi git push origin HEAD:tgi
cd /home/tgi/.hermes/hermes-agent
hermes update --yes
```

After successful update:

```bash
cd /home/tgi/.hermes/hermes-agent
git status --short --branch
git rev-parse --short=9 HEAD origin/tgi upstream/main
systemctl --user show hermes-gateway.service -p ActiveState -p SubState --no-pager
systemctl --user show titan-gateway.service -p ActiveState -p SubState --no-pager
systemctl --user show hermes-dashboard.service -p ActiveState -p SubState --no-pager
```

Clean up stale update artifacts only after `HEAD == origin/tgi` and focused tests pass:

```bash
git worktree list --porcelain
git stash list --date=local
# remove only known throwaway update worktrees
```

## Current divergence summary

Use `git rev-list --left-right --count upstream/main...HEAD` for the current left/right divergence. The non-merge TGI commits are grouped below by operational requirement; the live counts move whenever upstream advances or TGI adds patch commits.

### 1. Deploy-branch-safe updater

Commits:

- `8e876bf57` — `fix(update): preserve TGI deploy branch`
- `f348dc1b1` — `fix(update): harden TGI deploy branch reconciliation`
- `a400363a5` — `docs: document patched deploy branch sync`
- `47bbd3eed` — `test(update): force stale web dist in build assertion`
- This commit — `fix(update): port deploy resolver to TGI`
- `bc6648bbe6` — `fix(update): harden Windows completion handoff`

Primary files:

- `hermes_cli/banner.py` — deploy/upstream update check and `hermes version` preview ranges
- `hermes_cli/fork_update.py` — fork-owned deploy update helper implementation
- `hermes_cli/update_ui.py` — update progress/status helpers used by deploy handoff review and resolve output
- `hermes_cli/main.py` — deploy call-site seam plus TUI npm lockfile guard
- `hermes_cli/update_cmd.py` — Windows update preflight, receipt lifetime, and
  post-Desktop lockfile cleanup
- `hermes_cli/dashboard_procs.py` — concurrent-shim process classification
- `hermes_cli/subcommands/update.py`
- `tests/hermes_cli/test_update_autostash.py`
- `tests/hermes_cli/test_update_ui.py`
- `tests/hermes_cli/test_update_check.py`
- `tests/hermes_cli/test_version_preview.py`
- `tests/hermes_cli/test_cmd_update.py`
- `AGENTS.md`
- `website/docs/getting-started/updating.md`
- `website/docs/reference/cli-commands.md`

Why TGI needs it:

- The live runtime runs from `tgi`, not a clean upstream `main` checkout.
- A normal update that switches or resets to `main` would drop TGI Slack/runtime patches.
- Update conflicts must leave the live checkout untouched and hand off a retained temp worktree for manual or explicitly authorized agentic resolution.
- The helper implementation lives in `hermes_cli/fork_update.py` so upstream churn in `hermes_cli/main.py` only sees a small import seam.
- Dashboard web builds must install devDependencies even when the runtime environment exports `NODE_ENV=production`, otherwise `typescript`/`vite` can be omitted and stale dashboard assets may survive an update.
- CLI/Desktop-facing update checks must explain both deploy branch freshness (`HEAD..origin/tgi`) and upstream work not yet merged into the deploy artifact (`origin/tgi..upstream/main`).

Required behavior:

- Detect deploy branches such as `tgi` when no explicit update branch was requested.
- Fetch/sync upstream safely.
- Merge upstream into a temp worktree based on `origin/tgi`.
- On conflict, write/update the handoff marker, generate a human-readable update conflict review in `~/.hermes/update-reports/`, and automatically launch one non-interactive resolver attempt without modifying the live checkout.
- Bare `hermes update` owns the full deploy transaction: resume or rebuild a retained handoff when needed, validate, commit, push `HEAD:tgi`, fast-forward the live checkout, and continue install/restart handling.
- Retained handoff classification uses Git ancestry, not literal SHA text: already-published snapshots are cleared; handoffs based on an older `origin/tgi` tip are rebuilt once from the current tip; otherwise the retained worktree is resumed.
- The resolver must import the clean live Hermes CLI while retaining the conflict worktree as process cwd, because `hermes_cli/main.py` itself may be conflicted and unparsable.
- Deploy handoff progress must remain scrollback-safe: persistent phase lines only, no carriage-return spinner frames or ANSI clear-line output.
- Recover the common push race where another TGI host advances `origin/tgi` while an update is preparing its temp merge; retry once when reconciliation is safe before falling back to a handoff.
- `hermes version` should show the deploy branch and preview both pending deploy-branch commits and pending upstream commits.
- Resolver failures leave the live checkout unchanged and retain enough worktree/report context for safe retry or manual recovery.
- On Windows, a long-lived outer Hermes TUI/Desktop launcher remains visible
  to the concurrent-instance preflight even when it is an ancestor of the
  updater process. Refuse before backup, gateway pause, or source mutation;
  exempt only the updater Python process's immediate shim parent.
- The in-flight update receipt survives post-pull module-cache purging so a
  successful direct-Python completion replaces any earlier refused receipt.
- Desktop rebuilds restore only semantically verified npm annotation/optional-
  transitive lockfile churn. Meaningful dependency changes remain dirty and
  flow through the normal stash/preserve policy.
- TUI startup applies the same exact-byte semantic guard around its direct npm
  workspace install, including failed installs, so reopening Hermes cannot
  re-dirty the canonical lockfile after a successful update.
- After a manual push to `origin/tgi`, rerunning `hermes update --yes` fast-forwards live cleanly and refreshes install state.
- On Windows, an existing Hermes Desktop shortcut is durable install intent. `hermes update` must rebuild Desktop when source changed even if a failed or interrupted package step removed the source-tree `release/` and `dist/` artifacts.

Retirement criteria:

- Upstream supports named deploy/integration branches directly, including fork + upstream reconciliation, conflict handoff, live checkout protection, install refresh after manual handoff, and tests covering the TGI flow.
- Once upstream provides equivalent behavior, remove TGI-specific updater code and keep only configuration/docs that select the upstream mechanism.

Watch upstream for:

- `hermes update` branch-selection changes.
- PRs/issues mentioning deploy branches, integration branches, fork updates, upstream reconciliation, auto-stash, or update handoff recovery.
- Changes in `hermes_cli/main.py` around `_cmd_update_impl`, update fetch/pull/reset, web build, service restart, or config migration.

Focused tests:

```bash
venv/bin/python -m pytest -o 'addopts=' -q \
  tests/hermes_cli/test_update_autostash.py \
  tests/hermes_cli/test_update_ui.py \
  tests/hermes_cli/test_update_check.py \
  tests/hermes_cli/test_version_preview.py \
  tests/hermes_cli/test_cmd_update.py \
  tests/hermes_cli/test_update_interrupted_recovery.py \
  tests/hermes_cli/test_update_concurrent_quarantine.py \
  tests/hermes_cli/test_update_receipt.py \
  tests/hermes_cli/test_update_stale_module_purge.py \
  tests/hermes_cli/test_update_lockfile_churn.py \
  tests/hermes_cli/test_update_shim_self_lock.py \
  tests/hermes_cli/test_tui_npm_install.py
```

### 2. Desktop deploy-branch update visibility

Commits:

- This commit — `fix(desktop): support TGI deploy update visibility`

Primary files:

- `apps/desktop/electron/main.ts`
- `apps/desktop/src/global.d.ts`
- `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx`
- `apps/desktop/src/app/settings/about-settings.tsx`

Why TGI needs it:

- The Desktop updater UI previously only understood `main`/generic non-main branches. On `tgi`, the manual update prompt could degrade to `hermes update --branch tgi` even though the TGI deploy branch expects the richer bare `hermes update` deploy-flow semantics.
- Desktop update availability must remain tied to `HEAD..origin/tgi`, while upstream disparity should be shown separately as fork-maintenance context via `upstream/main...HEAD`.

Required behavior:

- Keep `tgi` in Desktop's deploy-branch allowlist so the UI preserves bare `hermes update` for TGI.
- Display upstream disparity (`upstream/main: +N carried`, `N behind`, or `aligned`) in Desktop status/About hints when an `upstream` remote exists.
- Never use upstream disparity as the update-available signal; `origin/tgi` freshness controls whether Desktop should offer an update.

Retirement criteria:

- Upstream Desktop supports deploy/integration branch metadata generically, separates branch freshness from upstream disparity, and keeps deploy branches on the correct bare update path.

Focused checks:

```bash
cd apps/desktop && npm run typecheck
```

### 3. Slack non-threaded/channel-session behavior

Commits:

- `cdc144dbc` — `fix: respect Slack non-threaded replies for streamed sends`
- `43172a097` — `fix(slack): preserve channel sessions for top-level messages`
- `74eed680d` — `fix(slack): preserve top-level channel context`

Primary files:

- `gateway/platforms/slack.py`
- `gateway/platforms/base.py`
- `gateway/run.py`
- `gateway/session.py`
- `gateway/config.py`
- `tests/gateway/test_slack.py`
- `tests/gateway/test_slack_channel_session_scope.py`
- `tests/gateway/test_slack_session_model.py`
- `tests/gateway/test_slack_approval_buttons.py`
- `tests/gateway/test_run_progress_topics.py`

Why TGI needs it:

- Atlas in `#agents` is intentionally `reply_in_thread: false` for normal top-level channel replies.
- Top-level Slack messages need a stable channel/user session so context accumulates across main-channel operations.
- Real Slack threads must remain separate side conversations keyed by Slack `thread_ts`.
- Progress/streamed sends must respect the same non-threaded policy as final replies.
- Top-level mentions should receive bounded recent channel context when appropriate.

Required behavior:

- With `reply_in_thread: false`, top-level channel messages use the channel/user main session instead of a synthetic timestamp session.
- Real thread replies stay keyed by Slack `thread_ts`.
- Progress/commentary/streamed sends do not accidentally thread when final replies are configured non-threaded.
- Thread/channel context fetches do not leak unrelated conversations into the wrong session.

Retirement criteria:

- Upstream natively supports Slack main-channel sessions plus thread side-conversations with equivalent routing metadata and tests.
- Upstream streamed/progress sends obey the same thread resolution as final sends.
- Upstream has a safe bounded channel-context model for explicit top-level mentions.

Watch upstream for:

- Slack adapter changes around `reply_in_thread`, `thread_ts`, `reply_to_message_id`, progress/status sends, `MessageSource.thread_id`, channel context, or session key generation.
- Gateway/session changes that introduce a formal Slack channel-session model.
- Tests added upstream for Slack thread vs top-level channel session behavior.

Focused tests:

```bash
venv/bin/python -m pytest -o 'addopts=' -q \
  tests/gateway/test_slack.py \
  tests/gateway/test_slack_mention.py \
  tests/gateway/test_slack_channel_session_scope.py \
  tests/gateway/test_slack_session_model.py \
  tests/gateway/test_stop_thread_sibling.py
```

### 4. Slack profile-branded slash passthrough

Commit:

- `8d27bfe73` — `fix: support profile-branded Slack slash passthrough`

Primary files:

- `gateway/platforms/slack.py`
- `tests/gateway/test_slack_mention.py`

Why TGI needs it:

- TGI's Slack app manifest may expose profile-branded slash commands such as `/atlas` while still routing to the normal Hermes gateway slash command implementation.
- This lets operators use familiar profile names without duplicating command plumbing.

Required behavior:

- Profile-name aliases route like `/hermes <subcommand>`.
- Configured aliases such as `atlas`, `titan`, or custom names are accepted as legacy passthrough names.

Retirement criteria:

- Upstream supports configurable slash command aliases or profile-branded slash commands with equivalent tests.

Watch upstream for:

- Slack slash command handling and `gateway/slash_commands.py` changes.
- Profile-aware gateway command routing.

Focused tests:

```bash
venv/bin/python -m pytest -o 'addopts=' -q tests/gateway/test_slack_mention.py
```

### 5. Slack assistant status UX

Commit:

- `383f53b22` — `fix(slack): improve assistant status feedback`

Primary files:

- `gateway/platforms/slack.py`
- `gateway/run.py`
- `tests/gateway/test_slack.py`

Why TGI needs it:

- Slack progress UX should be concise and not spray status messages into the main channel.
- When Slack supports native `assistant.threads.setStatus`, Hermes should use it; otherwise it should fall back to one concise status message in the thread.

Required behavior:

- Native Slack assistant status is used when available.
- Fallback status is controlled and not noisy.
- Native and fallback status state remains workspace-scoped when one gateway
  serves multiple Slack workspaces or Slack Connect channel IDs overlap.
- `reply_in_thread: false` final replies remain in channel while status/progress anchoring stays operationally useful.

Retirement criteria:

- Upstream ships native Slack assistant status handling or an equivalent low-noise progress model with tests.

Watch upstream for:

- Slack Assistant API/status support.
- Progress callback routing in `gateway/run.py`.
- `send_typing`, `send_progress`, or status-related Slack methods.

Focused tests:

```bash
venv/bin/python -m pytest -o 'addopts=' -q tests/gateway/test_slack.py
```

### 6. Live MCP/tool-schema refresh

Commit:

- `bb54c0219` — `fix: refresh live agent tool schemas after MCP updates`

Primary files:

- `agent/agent_init.py`
- `agent/chat_completion_helpers.py`
- `tests/agent/test_live_tool_schema_refresh.py`
- `tests/tools/test_mcp_tool.py`

Why TGI needs it:

- Atlas uses MCP-backed tools whose schemas can change during a long-running gateway process.
- Without a live schema refresh, the agent may keep stale required-argument schemas after MCP `notifications/tools/list_changed` updates.

Required behavior:

- Long-running agents notice tool registry generation changes.
- Tool schemas used for model calls refresh without needing a gateway restart.
- Changed MCP schemas replace old schemas even when tool names are unchanged.

Retirement criteria:

- Upstream has equivalent live tool schema invalidation/refresh behavior and regression tests.

Watch upstream for:

- MCP client changes.
- Tool registry generation/cache invalidation.
- `get_tool_definitions`, agent initialization, and chat completion helper changes.

Focused tests:

```bash
venv/bin/python -m pytest -o 'addopts=' -q \
  tests/agent/test_live_tool_schema_refresh.py \
  tests/tools/test_mcp_tool.py::TestMCPServerTask::test_refresh_tools_replaces_schema_for_unchanged_tool_name
```

### 7. Desktop remote profile handles

Commit:

- This commit — `feat(desktop): add TGI remote profile handles`

Primary files:

- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/src/global.d.ts`
- `apps/desktop/src/app/settings/gateway-settings.tsx`
- `apps/desktop/src/i18n/*.ts`
- `docs/refs/2026-06-tgi-desktop-remote-profile-handles.md`

Why TGI needs it:

- Upstream Desktop can save per-profile remote gateway overrides, but operators still have to create local stubs and copy connection settings by hand before remote profiles are visible in the profile rail.
- TGI needs a low-friction way to load named profiles from a remote gateway and pin them as local Desktop profile handles so the existing profile rail/sidebar becomes the local/remote agent switcher.
- The closest upstream design, draft PR #39337, is broad and stale; this TGI patch intentionally ports only the narrow discover-and-pin workflow.

Required behavior:

- Settings -> Gateway Connection shows a Remote profiles panel when the selected scope is remote.
- The panel can call the selected remote gateway's `/api/profiles` endpoint using the saved token/OAuth connection path.
- A remote profile can be added as a distinct local profile handle, then pinned to that remote gateway as a per-profile remote override. The local handle no longer has to match the remote profile name, so a remote `default` / Atlas can coexist with the local `default` / Atlas.
- Selecting that handle from the existing profile rail routes future Desktop traffic to the remote gateway; switching back to a local profile uses the local backend.

Retirement criteria:

- Upstream provides an equivalent or better Desktop workflow for discovering remote profiles/gateways, showing them beside local profiles, and switching without manual stub creation or token copying.
- Upstream routes chat/session/profile-scoped settings to the selected backend correctly and handles dead remotes visibly.
- Once upstream covers those outcomes, remove this TGI IPC/UI/string patch and keep the upstream implementation.

Watch upstream for:

- PR #39337 or successor peer-gateway/profile selector work.
- Desktop changes mentioning peer gateways, remote profile discovery, connection registry, gateway selector, per-profile routing, profile switch races, or model/session refresh on profile swap.

Reference:

- `docs/refs/2026-06-tgi-desktop-remote-profile-handles.md`

Focused checks:

```bash
cd apps/desktop && npm run typecheck
```

### 8. Desktop OAuth remote artifact opening

Commit:

- This commit — `fix(desktop): open OAuth remote artifacts from gateway session`

Primary files:

- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/src/global.d.ts`
- `apps/desktop/src/app/artifacts/index.tsx`
- `apps/desktop/src/lib/media.ts`
- `apps/desktop/src/lib/media.remote.test.ts`
- `apps/desktop/src/app/artifacts/index.test.ts`

Why TGI needs it:

- TGI's Desktop gateway connection now uses dashboard/basic auth (`authMode: oauth`) instead of the legacy session-token query path.
- Upstream's remote artifact download URL was token-mode biased: it only built `/api/files/download?...&token=...` when a saved token existed, so OAuth remote artifacts could fall back to `file://` paths that exist on the gateway host, not the Desktop client.
- Operators need the Artifacts panel to preview/open files produced by Atlas on the remote gateway after signing in through the dashboard flow.

Required behavior:

- Remote artifact image cards fetch gateway-local images through the authenticated REST bridge and include the owning profile when present.
- Opening a gateway-local artifact in remote OAuth mode asks Electron main to download it through the OAuth session partition, write a local temp copy, and open/reveal it via the OS.
- Token-mode remote artifacts keep using the existing token-authenticated path.
- Browser-native `http(s)` and `data:` artifacts remain normal external links/previews.

Retirement criteria:

- Upstream handles remote-gateway artifact previews and file opening through the active authenticated Desktop session for both token and OAuth/dashboard auth modes, including profile-scoped remote sessions.
- Once upstream covers those outcomes, remove this TGI IPC/UI/media patch and keep the upstream implementation.

Watch upstream for:

- Desktop changes touching Artifacts, `/api/files/download`, `/api/media`, `authMode: oauth`, remote gateway file previews, profile-routed REST, or Electron OAuth session requests.

Focused checks:

```bash
cd apps/desktop && npx vitest run --environment jsdom \
  src/lib/media.remote.test.ts \
  src/lib/desktop-fs.test.ts \
  src/app/artifacts/index.test.ts
cd apps/desktop && npm run typecheck
```

### 9. Desktop remote gateway file previews

Commit:

- This commit — `fix(desktop): preview remote gateway files through backend`

Primary files:

- `apps/desktop/electron/main.ts`
- `apps/desktop/src/lib/media.ts`
- `apps/desktop/src/lib/local-preview.ts`
- `apps/desktop/src/components/assistant-ui/markdown-text.tsx`
- `apps/desktop/src/lib/local-preview.test.ts`
- `apps/desktop/src/lib/media.remote.test.ts`
- `hermes_cli/web_server.py`
- `tests/hermes_cli/test_web_server_fs.py`
- `tests/hermes_cli/test_web_server_files.py`

Why TGI needs it:

- Desktop may run on Bailey's local PC while Atlas runs on `tgi-http` through a remote gateway. Backend-local paths such as `/home/tgi/.../report.html` are valid on the gateway host but invalid on the Desktop client.
- Upstream remote filesystem support covers source/data-url reads, but live HTML previews and some media/open paths could still become local `file://` URLs and fail with Electron `ERR_FILE_NOT_FOUND`.
- Operators expect generated HTML reports, preview links, file-browser previews, and media fallback opens to behave like a local gateway session: the app should read from the active backend, not from the client PC.

Required behavior:

- In remote mode, renderer-side preview target fallback skips Electron-local normalization for backend-local file paths.
- Backend-local preview targets use the `hermes-remote-file://<desktop-profile>/<absolute-path>` protocol for in-app live previews. Electron main resolves that protocol by fetching `/api/fs/preview` from the selected remote backend with the active token/OAuth session, and registers the handler for both the default Electron session and the `persist:hermes-preview` webview partition.
- Relative assets referenced by remote HTML remain under the same custom-protocol host/path so CSS/JS/images are fetched from the same backend directory.
- `/api/fs/preview` streams regular files inline with the same auth/path hardening as the existing remote filesystem API; query-token auth is allowed only for this browser-openable stream endpoint and `/api/files/download`.
- Remote media fallback/open behavior must not fall back to local `file://`; it should use the remote preview bridge or an Electron-authenticated local temp open rather than sending OAuth-only backend URLs to the system browser.
- File-browser/manual source previews continue to use `/api/fs/read-text` and `/api/fs/read-data-url`; remote file watching stays disabled because the client cannot watch the backend filesystem directly.

Retirement criteria:

- Upstream Desktop supports authenticated remote-gateway live file previews, including HTML with relative assets, OAuth/dashboard auth, profile-routed remote sessions, source/data-url previews, and remote media/open fallback paths without local `file://` leakage.
- Once upstream covers those outcomes, remove this TGI protocol/API patch and keep the upstream implementation.

Watch upstream for:

- PRs/issues mentioning remote gateway previews, `ERR_FILE_NOT_FOUND`, Desktop file browser, `/api/fs/preview`, `file://` conversion, `openPreviewInBrowser`, chat/media fallback downloads, or OAuth remote artifact opening.
- Current adjacent upstream work includes #44326, #44538, #46663, and #46895; keep checking whether their final landed form covers this whole behavior class.

Focused checks:

```bash
cd apps/desktop && npx vitest run --environment jsdom \
  src/lib/local-preview.test.ts \
  src/lib/media.remote.test.ts \
  src/lib/desktop-fs.test.ts \
  src/store/preview.test.ts \
  src/lib/preview-targets.test.ts
venv/bin/python -m pytest -o 'addopts=' -q tests/hermes_cli/test_web_server_fs.py tests/hermes_cli/test_web_server_files.py
cd apps/desktop && npm run typecheck
```

### 10. Cron failure delivery classifier for script tracebacks

Commit:

- This commit — `fix(cron): avoid mislabeling script tracebacks as provider timeout`

Primary files:

- `cron/scheduler.py`
- `tests/cron/test_scheduler.py`

Why TGI needs it:

- TGI runs deterministic script-only cron jobs for operational bridges such as the Lead-Time Board capacity email import.
- A script traceback can contain ordinary source text such as `urlopen(req, timeout=20)`. The prior delivery summarizer matched the word `timeout` anywhere in the traceback and reported `provider timeout`, hiding the real script/API failure from Slack.

Required behavior:

- Provider timeout summaries still apply to actual provider/fallback-chain timeout errors.
- Script failures with tracebacks are summarized as script failures unless provider context is present.
- Full details remain in cron output; Slack gets a compact but non-misleading alert.

Focused checks:

```bash
venv/bin/python -m py_compile cron/scheduler.py
venv/bin/python -m pytest -o 'addopts=' -q tests/cron/test_scheduler.py::TestCronFailureSummary
```

### 11. RETIRED — A2A reconnect carry

- **Former carry:** `fbe2a6c38d` (`fix(a2a): restore reconnect adapter contract`).
- **Retired:** 2026-08-02 after upstream merged A2A v1 in PR #77109.
- **Replacement:** upstream's adapter accepts gateway reconnect kwargs and ships
  substantially broader protocol, integration, and conformance coverage.
- **Resolution:** `plugins/platforms/a2a/*` and its tests now follow upstream;
  do not reapply the old TGI overlay.
- **Surviving seam:** `hermes_cli/plugins.py` still eagerly loads an explicitly
  enabled bundled platform plugin so A2A's outbound tools register. Keep the
  generic plugin discovery regression test until upstream absorbs that behavior.

### 12. Portable updater and Windows approval hardening

**Source provenance:** selectively carried from `origin/axiom` commits
`2674492748`, `0d3d3e71fd`, `8d04f10664`, `4e6003c599`, and `45c6016b57`.
The carries were adapted to TGI's newer updater architecture rather than taking
Axiom files wholesale.

**Protected outcomes:**

- Git Bash/MSYS absolute temp spellings are accepted for safe temporary-file
  deletion on Windows without weakening canonical-path checks on POSIX.
- Windows deploy updates wait for every force-killed gateway/launcher PID to
  exit before dependency replacement begins.
- An already-current checkout repairs a missing/stale installed Desktop build
  using TGI's existing install-intent and rebuild helper.
- Python dependencies are refreshed only when the relevant manifests changed;
  lazy dependency refresh, tool restoration, and memory-provider repair still
  run on every update.
- Deploy-update dependency comparison retains the pre-movement baseline after
  the tested deploy artifact advances the checkout.

**Primary files:** `tools/approval.py`, `hermes_cli/update_cmd.py`, and focused
tests under `tests/tools/` and `tests/hermes_cli/test_update_*`.

**Focused verification:**

```bash
venv/bin/python -m pytest -q -o addopts='' \
  tests/tools/test_approval.py \
  tests/hermes_cli/test_update_concurrent_quarantine.py \
  tests/hermes_cli/test_update_current_node_repair.py \
  tests/hermes_cli/test_update_python_dependency_refresh.py \
  tests/hermes_cli/test_cmd_update.py \
  tests/hermes_cli/test_update_autostash.py \
  tests/hermes_cli/test_update_check.py
```

**Retire when:** upstream provides equivalent behavior for each invariant and
the focused tests pass after removing that local hunk. Evaluate each outcome
independently; this group does not need to retire atomically.

### 13. Axiom Enhancements runtime-plugin host contract

**Distribution:** the plugin source is not bundled into this public fork. The
private Axiom Agent Library owns the canonical `axiom-enhancements` disk-plugin
artifact and assigns it to the `tgi-desktop` surface. TGI core carries only the
generic SDK/layout seams required to load that artifact safely.

**Source provenance:** selectively adapted from `origin/axiom` commits
`e0b3427ea9`, `c4fdb79941`, `1e41e7cd5b`, and `eac0ee14b9`.

**Required behavior:**

- `host.updates.getStatus/open` exposes detached client/backend snapshots and
  delegates mutation to the core-owned updater.
- A plugin may opt a singleton pane into `closeBehavior: 'dismiss'`, preserve
  that dismissal across registry refreshes, and reopen it through the
  namespaced `ctx.panes.reveal(...)` API.
- `ctx.panes.collapse(...)` minimizes only the calling plugin's namespaced pane.
- `sidebar.nav` contributions may use an `onSelect` action instead of a route,
  allowing the plugin to reopen its pane without replacing the active workspace.
- The staged update mutation lifecycle (`prepare`, `restartAndApply`, history,
  upstream sync) is intentionally absent. The plugin feature-detects these
  optional methods and hides unavailable actions. Do not add stubs or expose a
  partial Electron/Python/PowerShell lifecycle.

**Primary files:** `apps/desktop/src/sdk/index.ts`,
`apps/desktop/src/contrib/plugin.ts`, pane-tree store/tests, sidebar contribution
types/rendering, and the public Desktop plugin SDK guide.

**Focused verification:**

```bash
cd apps/desktop
NODE_ENV=test npm run typecheck
NODE_ENV=test npx vitest run --environment jsdom \
  src/sdk/index.test.ts \
  src/contrib/plugin.test.ts \
  src/components/pane-shell/tree/pane-toggle-visibility.test.ts
```

**Retire when:** upstream exposes equivalent typed update snapshots/core updater
delegation plus dismiss/reveal/collapse/sidebar-action plugin seams. The external
plugin remains private library content even after the core seams retire.

**Deferred separately:** Axiom's newer remote-profile correctness stack spans
effective backend routing, native OAuth, reconnect, cold-start state, ownership,
and profile lookup. It is an eight-commit architecture port and was not folded
into this production sync.

### 14. Desktop remote-display GPU policy control

**Upstream provenance:** PR #53991 already provides the canonical
`desktop.disable_gpu` config/CLI launch bridge. PR #73471 attempted a binary UI
control but was closed because it destroyed the required `auto` state; its
review explicitly called for a tri-state design.

**Required behavior:**

- Settings → Advanced exposes `Automatic`, `GPU on`, and `Software rendering`.
- `Automatic` preserves upstream RDP/SSH/X11 flicker protection.
- `GPU on` deliberately overrides remote-display detection for responsive RDP
  sessions where software compositing is too expensive.
- `Software rendering` remains available for known-bad GPU/compositor hosts.
- The setting states that a Desktop restart is required.
- Direct Start/Desktop/taskbar launches read the same profile-scoped
  `config.yaml` policy before `app.ready`; they must not silently bypass the
  config bridge merely because they did not launch through `hermes desktop`.
- An explicit `HERMES_DESKTOP_DISABLE_GPU` environment value remains the highest
  precedence rung for recovery and diagnostics.

**Primary files:** `hermes_cli/web_server.py`, Desktop Advanced settings copy,
`apps/desktop/electron/desktop-gpu-policy.ts`, and the pre-ready GPU decision in
`apps/desktop/electron/main.ts`.

**Focused verification:**

```bash
python -m pytest -o 'addopts=' -q \
  tests/hermes_cli/test_desktop_gpu_policy_schema.py \
  tests/hermes_cli/test_gui_command.py -k 'desktop_launch_options or gpu_policy_schema'
cd apps/desktop
npm run typecheck
node --import <tsx-loader> --test electron/desktop-gpu-policy.test.ts
npx vitest run src/app/settings/helpers.test.ts
```

**Retire when:** upstream ships a tri-state GPU policy UI and direct packaged
launches honor `desktop.disable_gpu` with equivalent precedence and tests.

## Current known update/build pitfalls

### 7. Anthropic Claude OAuth billing-lane fixes

**Outcome:** Anthropic OAuth requests via Claude subscription use the correct wire format so tool calls, tool_choice enforcement, and system prompt caching work on the Claude Code billing lane.

**Upstream status:** Not yet merged. PRs #23361 and #47738 are open upstream; the Axiom fork carries them on `origin/axiom` and TGI mirrors the same stack on `origin/tgi`.

**What changed:**

- `tool_choice` name encoding: concrete tool_choice names use the same `mcp__` wire-name encoding as the tools array (from #23361).
- System prompt relocation: large Hermes system prompts are relocated out of Anthropic `system[]` into a cache-marked first-user `<system_context>` preamble on the OAuth path (from #47738).
- Conflict resolution: double-underscore response-strip prefix preserved after stack application.

**Primary files:**

```text
agent/anthropic_adapter.py
agent/transports/anthropic.py
tests/agent/test_anthropic_adapter.py
tests/agent/test_anthropic_oauth_system_relocation.py
```

**Verification:**

```bash
python3 -m py_compile agent/anthropic_adapter.py agent/transports/anthropic.py
venv/bin/python -m pytest tests/agent/test_anthropic_adapter.py tests/agent/test_anthropic_oauth_system_relocation.py -q -o 'addopts='
```

**Remove when:** upstream merges #23361 and #47738 into `main` and a subsequent `hermes update` brings them into the TGI checkout.

### Web build under `NODE_ENV=production`

The TGI service environment can carry `NODE_ENV=production`. If `npm install --workspace web` omits dev dependencies, `hermes update` may fast-forward successfully but print `tsc: not found` and serve stale web dist.

Repair from the live checkout:

```bash
cd /home/tgi/.hermes/hermes-agent
NODE_ENV=development npm install --workspace web --include=dev
NODE_ENV=development npm run build --workspace web
systemctl --user restart hermes-dashboard.service
```

Verify:

```bash
curl -fsS http://127.0.0.1:9119/api/status
stat hermes_cli/web_dist/index.html
```

### Slack test env leakage

Some config-bridging tests call `load_gateway_config()`, which intentionally writes Slack settings into environment variables. If later Slack session tests inherit `SLACK_ALLOWED_CHANNELS`, they may drop otherwise-valid messages.

Mitigation:

- Clear Slack env vars in test fixtures, or
- Set fixture-local `adapter.config.extra["allowed_channels"] = ""`, `free_response_channels = ""`, `strict_mention = False`, and `require_mention = True` for tests that assert mention/session routing.

## Divergence review checklist

Run this after every upstream merge or before deciding whether to remove a patch:

```bash
cd /home/tgi/.hermes/hermes-agent
git fetch upstream origin --prune
git status --short --branch
git rev-list --left-right --count upstream/main...HEAD
git log --oneline --no-merges upstream/main..HEAD
git diff --name-only upstream/main..HEAD
```

For each TGI patch group:

1. Identify the operational outcome it protects.
2. Search upstream commits/PRs/issues for equivalent behavior.
3. If upstream covers the outcome, remove the local delta in a temp branch/worktree.
4. Run the focused tests for that group.
5. Update this file and TGI docs.
6. Push only after the live update path can fast-forward cleanly.

## Documentation references

- Host/runtime quick reference: `/home/tgi/SYSTEM.md`
- Agent deployment note: `/home/tgi/obsidian-vault/1_Projects/TGI/TGI Hermes Agents.md`
- Update handoff skill reference: `hermes-agent` skill, `references/2026-06-tgi-deploy-branch-update-merge.md`
- Repo development guide: `AGENTS.md`

## Maintenance rule

If a new TGI-only commit is added to `tgi`, update this file in the same commit or the immediately following docs commit. Undocumented divergence should be treated as technical debt and reviewed before the next `hermes update` handoff.
