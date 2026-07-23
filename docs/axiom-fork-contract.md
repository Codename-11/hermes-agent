# Axiom Hermes Fork Contract

This repository is Bailey/Axiom's deploy fork of `NousResearch/hermes-agent`.

## Branch and remote contract

- Canonical upstream: `upstream/main` (`https://github.com/NousResearch/hermes-agent.git`).
- Axiom deploy branch: `origin/axiom` (`https://github.com/Codename-11/hermes-agent.git`).
- Axiom-Desktop install path: `%LOCALAPPDATA%\hermes\hermes-agent`.
- Axiom-Desktop tracks `origin/axiom`; do not silently switch it back to upstream `main`.
- Bare `hermes update`, `hermes update --check`, and `hermes --version` are intentionally deploy-branch-aware on `axiom`; operators should not need a special Desktop-only update command. On a deploy branch, plain `hermes update` consumes the tested `origin/<deploy>` artifact and never performs upstream integration implicitly.
- Desktop's update UI should distinguish deploy-branch freshness from upstream disparity: it checks `HEAD..origin/axiom` for update availability and also surfaces `upstream/main` ahead/behind counts so Axiom can see carried fork delta without treating it as an update blocker.
- If upstream has new commits but `origin/axiom` has not moved, Desktop's **client** or backend runtime update may show upstream disparity, but it should not present that as an installable update or start a merge. Upstream integration requires `hermes update --resolve` on an intentional merge-authority host or the dedicated sync job.

## Status and source-of-truth contract

Use these surfaces deliberately; do not duplicate live branch counts by hand:

| Surface | Purpose |
| --- | --- |
| `docs/axiom-fork-contract.md` | Canonical concise branch/Desktop/update contract. |
| `FORK.md` | Protected behavior inventory, retired fork surface, historical fork-only commit classification, and validation expectations. |
| `scripts/fork-status.py` | Read-only live status report for branch divergence, dirty files, Sentinel sync state, and optional Axiom-Desktop SSH branch check. |
| Obsidian `3. System/Operations/Hermes Axiom Sync Runbook.md` | Operational procedure for upstream integration and live deployment separation. |
| `~/SYSTEM.md` / Hermes Overview | Quick-start pointers only; keep them short and point back here/runbook. |

Before updating, resolving conflicts, or checking whether Docker-Server and Axiom-Desktop are aligned, run:

```bash
cd ~/.hermes/hermes-agent
scripts/fork-status.py
scripts/fork-status.py --desktop   # optional; read-only, requires reachable Windows SSH
```

## Update contract

The `axiom` branch is expected to:

1. Pull/merge upstream `main` regularly.
2. Carry a small, documented patch layer for Axiom-specific deployment and Desktop stability.
3. Keep fork-only patches narrow and easy to drop when upstream lands equivalent fixes.
4. Run focused Desktop checks before pushing patches that affect Axiom-Desktop.
5. Treat upstream disparity as a maintenance signal, not a deploy blocker. `origin/axiom` freshness controls Axiom-Desktop updates; `upstream/main...HEAD` tells maintainers when to review drift.
6. Do not rely only on merge conflicts to retire fork patches. Conflicts catch same-line overlap, but upstream can land a better adjacent/architectural fix that merges cleanly. During each upstream merge, review the carried Desktop patch layer and drop local fixes only after verifying upstream has an equivalent or better behavior.
7. Keep fork-only code that has clean boundaries in **fork-owned modules**, not inline in upstream hotspot files. The deploy-branch update flow lives in `hermes_cli/axiom_update.py` (extracted from `main.py` on 2026-06-21; expanded with resolve/consume helpers on 2026-06-25) with a thin import seam back into `main.py`. Upstream never edits a filename it does not ship, so these carry with ~zero merge surface. See FORK.md → "Fork footprint reduction" for the seam contract and the lazy-import rule that avoids the circular import. When adding new fork-only update/deploy logic, put it in `axiom_update.py`, not back in `main.py`.
8. `hermes update --resolve` is explicit authorization for autonomous deploy-handoff resolution. It may run a Hermes resolver in the retained temp worktree, validate no unmerged files/conflict markers remain, run matched focused checks, commit/push `HEAD:<deploy>`, fast-forward the live checkout, and then continue the normal install/restart phase. It must hard-stop on sensitive paths or ambiguous git state.
9. Plain `hermes update` and explicit `hermes update --consume` are the runtime-safe path on every deploy branch: consume only `origin/<deploy>` and do not merge `upstream/main` from that host. Use `--resolve` when the current host is intentionally acting as merge authority.

Suggested focused verification for Desktop patch work:

```bash
node --check apps/desktop/electron/main.cjs
cd apps/desktop
npm run typecheck
NODE_ENV=test npm run test:ui -- \
  src/app/right-sidebar/files/use-project-tree.test.ts \
  src/app/session/hooks/use-prompt-actions.test.tsx \
  src/store/session.test.ts
```

Run React/Vitest slices with `NODE_ENV=test`. A production ambient env can make Testing Library resolve React's production build and fail with `TypeError: React.act is not a function`, even when the Desktop code is fine. If Vitest's jsdom localStorage shim fails in `src/store/session.test.ts`, rerun with the repo's `npm run test:ui -- ...` harness and record the exact failure. Do not treat unrelated harness failures as proof that the Desktop patch is broken.

## Current Desktop remote-file/access layer

Axiom-Desktop often runs the Electron shell on Windows while the active Hermes gateway/profile is remote. That means a path like `/home/.../artifact.png` usually exists on the gateway, **not** on the Windows client.

Keep this section as the tracking source for remote file/sidebar/artifact behavior. The rule is: prefer upstream-merged fixes, carry only narrow/drop-ready patches on `axiom`, and do not revive older workaround commits unless Bailey explicitly asks for a new local behavior.

### Current state at a glance

| Surface | Current behavior | Source | Notes |
| --- | --- | --- | --- |
| Files/sidebar browse + preview | Remote Desktop sessions use authenticated read-only `/api/fs/*` backend REST for listing, capped text preview, capped data URLs, git-root detection, and backend default cwd. Local Desktop sessions keep Electron filesystem IPC. | Upstream #44326 | Supersedes the older local-only ENOENT workaround path from #42497/#43082. |
| OS/Finder drops into remote sessions | Local dropped files upload/stage into the remote session workspace instead of leaking Windows/macOS absolute paths into prompts. | Upstream #43109 | Keeps drag/drop usable when chat runs on a remote gateway. |
| Remote profile switches | Remote-aware surfaces follow the active profile/backend after profile switches. | Upstream #46658 | Covers `image.attach_bytes`, `/api/fs/*`, and `/api/media`. |
| Global remote/Docker profile REST | Desktop appends the active profile to global-remote profile-scoped REST calls and profile-scopes OAuth status/start/completion. | Upstream #47011 | Needed when many profiles share one remote backend. |
| Artifacts/generated files | Remote artifacts and generated files open through authenticated `/api/files/download?path=…&token=…`, not gateway-host `file://` paths on Windows. | Upstream #46895 | Endpoint stays auth-gated; query token is allowed only for `/api/files/download`, not public files access. |
| Chat/media fallback link UX | Axiom keeps the #44538 wrapper/error-state behavior for chat/media fallback links. | Axiom carry of upstream PR #44538 | Re-evaluate once upstream merges #44538 or fully covers chat/media fallback UX through `/api/files/download`. |

### Still carried on `axiom`

| Carry | Why it remains | Drop/review condition |
| --- | --- | --- |
| Upstream PR #44538 — `fix(desktop): remote-mode chat file links download via the fs bridge instead of dead file:// URLs` | Provides chat/media fallback download handling and visible error state beyond the shared artifact download path. | Drop once upstream merges #44538 or equivalent chat/media fallback UX is present on `main`. |
| Upstream PR #42603 — `fix(desktop): narrow hover-reveal trigger strip to avoid blocking scrollbar` | Small Desktop UX fix; prevents collapsed side-panel hover activation from stealing the scrollbar hit area. | Drop once equivalent behavior is verified on `main`. |
| Upstream PR #41189, focused manual port — `fix(desktop): raise FILE_BROWSER_MAX_WIDTH from 20rem to 40rem` | One-line width improvement; original PR head had unrelated merge noise. | Drop once equivalent width is verified on `main`. |

### Retired or superseded — do not reintroduce by default

| Item | Why not |
| --- | --- |
| `e830ac3e6` — `fix(desktop): skip remote session cwd in Files panel to prevent ENOENT` | Superseded by upstream #44326's actual remote Files/sidebar browsing. |
| `b6d71f248` — `fix desktop remote cwd workspace drift` | Superseded by upstream remote filesystem/default-cwd routing. |
| `8e5b55378` — `docs: clarify local files pane in remote mode` | The Files pane is no longer documented as local-only in remote mode. |
| Upstream PR #40090 — `remote file browser via dashboard API` | Superseded by upstream #44326. |
| Upstream PR #46663 — `convert file:// URLs to HTTP download for remote gateways` | Superseded by upstream #46895, which salvaged the behavior without CRLF noise and without making the endpoint public. |

### Deferred larger scope

| PR | Status | Reason |
| --- | --- | --- |
| Upstream PR #39122 — `remote workspace + terminal over SSH` | Not carried | Broad Electron SSH/terminal/settings/file-browser architecture change. Evaluate in a dedicated branch. |
| Upstream PR #39183 — `edit files in place from the preview pane` | Not carried | Stacked on #39122 and introduces remote write-back/editing. Do not mix it into the read-only browse/preview lane. |

## Axiom-Desktop operational notes

After pushing `origin/axiom`, update Axiom-Desktop with:

```powershell
cd $env:LOCALAPPDATA\hermes\hermes-agent
hermes update
```

Verify:

```powershell
git status --short --branch
git log -5 --oneline
hermes --version
Get-Item apps\desktop\release\win-unpacked\Hermes.exe | Select-Object FullName,Length,LastWriteTime
```
