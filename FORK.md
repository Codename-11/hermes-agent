# Axiom Hermes Fork Contract

Generated: 2026-06-08T12:05:55-04:00  
Repo: `/home/bailey/.hermes/hermes-agent`  
Deploy branch: `origin/axiom`  
Upstream: `upstream/main`

This file is the contract for the Axiom-maintained Hermes fork. Its purpose is to prevent upstream merges from silently dropping Hermes-Relay, Forge, Discord, proxy, update/deploy, or local Axiom operations behavior.

## Current fork state

As of the generation timestamp above, after fetching `origin` and `upstream`:

```text
origin/axiom...upstream/main = 179 / 178
axiom...origin/axiom          = 0 / 58
fork-only non-merge commits   = 95
fork-only total commits       = 179
upstream missing non-merge    = 172
upstream missing total        = 178
origin/axiom head             = 0d7f6b4d
upstream/main head            = 74239b494
```

Interpretation:

- `origin/axiom` contains a substantial fork-only patch surface.
- `upstream/main` has a substantial backlog not yet integrated into `origin/axiom`.
- The live local checkout branch `axiom` is behind `origin/axiom`; do not assume local `HEAD` represents the deploy branch.
- The automated daily sync job is paused until this contract is hardened and the fork-only surface is reviewed.

Paused cron job:

```text
job_id: 44f7334c4efc
name: Hermes Axiom Sync
script: sentinel-hermes-axiom-sync.py
schedule: 0 5 * * *
state: paused
paused_at: 2026-06-08T12:04:38.045286-04:00
```

## Rules for upstream merge resolution

1. **Never resolve conflicts by taking all upstream or all fork changes in protected files.** Protected behavior below must be explicitly preserved, retired, or replaced.
2. **Resolve from a fresh worktree based on `origin/axiom`, not the live checkout, unless the live checkout has first been fast-forwarded intentionally.**
3. **Before pushing a resolved merge to `origin/axiom`, run the fork contract tests listed in this file.** Add missing tests before trusting manual review.
4. **When upstream has refactored a hotspot file, port Axiom behavior into upstream's new split/module rather than re-expanding the old god file.**
5. **If a feature is obsolete because upstream now provides equivalent behavior, mark it retired in this file and add verification evidence.**
6. **Do not resume the daily sync cron until conflict alerts are deduped and the contract tests cover the protected Axiom behavior.**

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
- Synthetic/fallback model inventory must not advertise stale or non-chat model IDs to downstream routers.
- Local provider/model config values may be dict-shaped and must not crash model resolution.

Known references:

- `DEVLOG.md` 2026-05-19: routed proxy model inventory and `gpt-5.5` advertisement.
- `DEVLOG.md` 2026-04-23: Anthropic OAuth shim cleanup while preserving Model Router/plugin-command seams.
- `DEVLOG.md` 2026-06-17: Claude OAuth billing-lane candidate stack (#47723 + #23361 + #47738) and live `claudetest` smoke.

Primary files:

```text
hermes_cli/proxy/*
agent/anthropic_adapter.py
gateway/builtin_hooks/boot_md.py
hermes_cli/runtime_provider.py
tests/hermes_cli/test_proxy.py
tests/agent/test_anthropic_adapter.py
```

### 6. Update / deploy branch behavior

Protected behavior:

- Axiom deploy branch strategy remains explicit: upstream merges into `origin/axiom`; live checkout updates happen in a separate maintenance/update step.
- `hermes update` / update checks understand fork deploy branches and do not incorrectly declare up-to-date by checking only `origin`.
- Deploy branch updates are transactional and preserve rescue prompts for stash/merge conflicts.
- Update path can restart named profile gateway services without leaking profile env between processes.
- Update path excludes systemd-managed dashboard/Desktop child processes from unsafe kill sweeps.
- Windows quarantine safeguards remain intact.
- Pipeline TUI / update handoff context remains agent-readable.

Known references:

- `DEVLOG.md` 2026-06-03: upstream sync and service refresh.
- fork-only commits tagged `fix(update)`, `feat(update)`, `fix(banner)`, `fix(version)`, `fix(cli)`.

Primary files:

```text
hermes_cli/main.py
hermes_cli/banner.py
hermes_cli/update_ui.py
hermes_cli/gateway.py
hermes_cli/web_server.py
scripts/check-merge-drops.py
tests/hermes_cli/test_update_check.py
tests/hermes_cli/test_update_autostash.py
tests/hermes_cli/test_update_stale_dashboard.py
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

## Temporary upstream PR carries

Carried commits from open upstream PRs are merged into `axiom` with `--no-ff` from a dedicated `carry/upstream-pr-<number>-<topic>` branch so the carry can be reverted as a unit when upstream merges or replaces the feature.

### Desktop model picker snap-back — local carry pending upstream equivalent

- **Status:** LOCAL TEMPORARY CARRY — no upstream PR opened at operator direction.
- **Local subject:** `fix(desktop): keep model picker selection authoritative`
- **Why carried:** Desktop can briefly render stale `model.options` query metadata over the live composer/session stores after an explicit picker selection, making the UI snap back to the prior model/provider and allowing test chats to launch against the wrong runtime.
- **Files touched:** `apps/desktop/src/app/session/hooks/use-model-controls.ts`, `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx`, `apps/desktop/src/app/shell/model-menu-panel.tsx`, `apps/desktop/src/components/model-picker.tsx`.
- **Drop condition:** If upstream changes Desktop model controls so explicit picker selections are store-authoritative, route through the live active session, and include equivalent regression coverage, drop this local carry instead of preserving duplicate fork behavior.
- **Verification:** `NODE_ENV=test npm run test:ui -- src/app/session/hooks/use-model-controls.test.tsx` → 6 passed; `NODE_ENV=test npm run typecheck` → OK; touched-file eslint → OK.

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
11b81f69a fix: /personality now overrides SOUL.md identity via ephemeral_system_prompt priority (axiom fork)
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
  agent/runtime_tool_policy.py
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
