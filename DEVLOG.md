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
