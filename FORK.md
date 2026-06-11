# TGI Fork Contract

Last reviewed: 2026-06-10  
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
- `hermes update` should reconcile `upstream/main` into `origin/tgi` in a temporary worktree, then fast-forward the live checkout.
- `hermes update`, `hermes update --check`, and `hermes --version` are intentionally deploy-branch-aware on `tgi`; operators should not need a special Desktop-only update command.
- Desktop's **client** update UI should use `HEAD..origin/tgi` for installable update availability and `upstream/main...HEAD` only for fork-disparity visibility.
- Desktop's **backend** update UI is different: it should prompt when `hermes update` would do useful work, including upstream commits not yet merged into `origin/tgi`. Show the count breakdown (`HEAD..origin/tgi` plus `origin/tgi..upstream/main`) so the operator sees why the backend update is actionable.
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
/home/tgi/.hermes/hermes-agent/venv/bin/python -m py_compile gateway/run.py gateway/platforms/slack.py gateway/session.py hermes_cli/main.py
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

Primary files:

- `hermes_cli/main.py`
- `tests/hermes_cli/test_update_autostash.py`
- `tests/hermes_cli/test_cmd_update.py`
- `AGENTS.md`
- `website/docs/getting-started/updating.md`
- `website/docs/reference/cli-commands.md`

Why TGI needs it:

- The live runtime runs from `tgi`, not a clean upstream `main` checkout.
- A normal update that switches or resets to `main` would drop TGI Slack/runtime patches.
- Update conflicts must leave the live checkout untouched and hand off a retained temp worktree for manual resolution.

Required behavior:

- Detect deploy branches such as `tgi` when no explicit update branch was requested.
- Fetch/sync upstream safely.
- Merge upstream into a temp worktree based on `origin/tgi`.
- On conflict, write/update the handoff marker and do not damage the live checkout.
- After manual push to `origin/tgi`, rerunning `hermes update --yes` fast-forwards live cleanly and refreshes install state.

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
  tests/hermes_cli/test_update_check.py \
  tests/hermes_cli/test_cmd_update.py \
  tests/hermes_cli/test_update_interrupted_recovery.py
```

### 2. Desktop deploy-branch update visibility

Commits:

- This commit — `fix(desktop): support TGI deploy update visibility`

Primary files:

- `apps/desktop/electron/main.cjs`
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
node --check apps/desktop/electron/main.cjs
cd apps/desktop && npm run type-check
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

## Current known update/build pitfalls

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
