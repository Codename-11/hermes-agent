# Hermes Agent — Dev Log

## 2026-04-22 — Claude Max OAuth "credit balance too low" fix

### Problem
Hermes routing via Claude Code OAuth token (MAX subscription) failed with:
`HTTP 400: Your credit balance is too low to access the Anthropic API`
Root cause: Anthropic counts the `system` param against Max subscription token budget.
Hermes verbose prompts (Victor persona + tools + memory) exceeded that budget, routing
to extra-usage pool → depleted → 400.

### Fix
`agent/anthropic_adapter.py` — `build_anthropic_kwargs(is_oauth=True)`:
- `_extract_system_for_oauth()`: captures system content BEFORE `convert_messages_to_anthropic()` strips it
- `_sanitize_oauth_reminders()`: sanitizes product names and returns reminder text fragments
- OAuth path now keeps `system` limited to Claude Code-required entries: signed `x-anthropic-billing-header` + Claude Code identity block
- Remaining system text is prepended to the first user message as normal Anthropic `text` content whose `text` contains literal `<system-reminder>...</system-reminder>` tags
- Important implementation detail: Anthropic rejects typed `{ "type": "system_reminder" }` blocks; the workaround only works with literal tag text inside a normal `text` block
- Added runtime helpers `anthropic_oauth_prompt_shim_enabled()` + `anthropic_oauth_prompt_shim_label()`
- Added Claude Code fingerprint parity from `kristianvast/hermes-claude-auth`: `sdk-cli` billing header, extra OAuth betas, `x-stainless-*` headers, `anthropic-dangerous-direct-browser-access`, and `extra_query.beta=true`
- OAuth tool naming now matches Claude Code for built-ins (`bash` → `mcp_Bash`) while preserving round-trip identity for existing Hermes `mcp_*` tools
- Community upstream: refs NousResearch/hermes-agent#10575, #10576 (not yet merged); local reference: `kristianvast/hermes-claude-auth`

### UI visibility
Added explicit runtime status when the shim is active:
- CLI full startup banner shows `Auth: Anthropic OAuth + prompt shim`
- CLI compact startup status line appends the same label
- CLI live status bar shows the label on wide terminals
- TUI session panel shows an `Auth` section with the label
- TUI live status bar shows the label on wide terminals
- New `/auth` command (CLI + gateway) reports the active auth source, model, API mode, base URL, and whether the Anthropic OAuth prompt shim is active

### Verification
- `python3 -m py_compile scripts/test_anthropic_oauth_smoke.py` → OK
- `pytest -q tests/agent/test_anthropic_adapter.py tests/agent/test_auxiliary_client_anthropic_oauth.py tests/agent/test_anthropic_normalize_v2.py tests/gateway/test_auth_command.py tests/run_agent/test_anthropic_third_party_oauth_guard.py` → 170 passed
- `hermes auth reset anthropic` → cleared exhausted Claude Code OAuth status so the fixed path becomes primary again
- Canonical smoke test: `source venv/bin/activate && python3 scripts/test_anthropic_oauth_smoke.py` → PASS
- Direct OAuth smoke request via `build_anthropic_kwargs(..., is_oauth=True)` + `build_anthropic_client(...)` → success
- Direct OAuth smoke usage markers: `service_tier=standard`, `inference_geo=not_available`
- End-to-end CLI smoke: `hermes chat -q 'Reply with exactly: AUTH TEST OK' --provider anthropic -m claude-haiku-4-5 -Q` → `AUTH TEST OK`
- Confirmed wire shape: `system` contains billing header + Claude Code identity; first user message contains literal `<system-reminder>` tag text in a normal `text` block

### Files changed
- `agent/anthropic_adapter.py`: shim helpers + OAuth prompt transform
- `cli.py`, `hermes_cli/banner.py`: CLI startup/status visibility
- `tui_gateway/server.py`, `ui-tui/src/components/*`, `ui-tui/src/types.ts`: TUI session/status visibility
- Targeted tests for adapter, banner, CLI status bar, TUI session info

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
