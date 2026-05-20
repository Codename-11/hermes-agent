# Hermes Agent — Dev Log

## 2026-05-20 — Add roundtable circuit breaker plugin

### Summary

Added a bundled `roundtable-orchestrator` plugin as a runtime brake for Discord multi-agent rooms. The plugin does not replace Discord bot-admission policy or create a new orchestration path; it adds `/roundtable status|stop|start` and a shared fail-closed pre-dispatch gate for admitted Discord bot-authored events.

### What changed

- Added `plugins/roundtable_orchestrator/` with a gateway-only `/roundtable` command.
- Added shared state at `~/.hermes/roundtable_state.json`, overrideable via `HERMES_ROUNDTABLE_STATE`.
- Added optional roundtable channel scoping via `HERMES_ROUNDTABLE_CHANNELS` / `DISCORD_ROUNDTABLE_CHANNELS` or `discord.roundtable.channels`.
- Added `pre_gateway_dispatch` coverage so stopped roundtables skip admitted Discord bot turns before any LLM call.
- Updated focused gateway/plugin tests and isolated roundtable env vars in Discord roundtable tests.

### Verification

- `python -m py_compile plugins/roundtable_orchestrator/__init__.py gateway/run.py gateway/platforms/discord.py` → OK
- `python -m pytest tests/gateway/test_roundtable_orchestrator_dispatch.py -q -o 'addopts='` → 5 passed.
- `python -m pytest tests/plugins/test_roundtable_orchestrator_plugin.py tests/gateway/test_roundtable_orchestrator_dispatch.py tests/gateway/test_discord_roundtable.py -q -o 'addopts='` → 29 passed.

## 2026-05-20 — Add Discord roundtable safety controls

### Summary

Added a focused Discord gateway patch for human-facilitated multi-profile rooms. The patch keeps solo-bot behavior unchanged by default, adds config parity for bot-authored message admission, and introduces opt-in roundtable safeguards so Victor/Mizu/Sentinel-style profiles can see each other's context without accidental bot-to-bot cascades.

### What changed

- Added `discord.allow_bots` config support matching `DISCORD_ALLOW_BOTS=none|mentions|all`.
- Added `discord.roundtable` controls for safe multi-agent rooms: `enabled`, `include_bot_history`, `outbound_bot_mentions`, and `participant_bot_ids`.
- Refactored Discord bot-message admission into tested adapter helpers.
- Made history backfill use the normalized bot-history policy instead of env-only checks.
- Escapes configured participant bot mentions in outbound Discord replies when roundtable mode is enabled, preventing accidental live pings to other Hermes bots.
- Documented the human-facilitated roundtable pattern in the Discord messaging docs and Hermes skill reference.

### Verification

- `python -m py_compile gateway/platforms/discord.py gateway/config.py hermes_cli/config.py tests/gateway/test_discord_roundtable.py tests/gateway/test_discord_send.py` → OK
- `python -m pytest tests/gateway/test_discord_roundtable.py tests/gateway/test_discord_bot_filter.py tests/gateway/test_discord_send.py -q -o 'addopts='` → 42 passed.
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
