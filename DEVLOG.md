# Hermes Agent — Dev Log

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
