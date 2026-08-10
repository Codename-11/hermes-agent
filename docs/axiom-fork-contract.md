# Axiom Hermes Fork Contract

This repository is Bailey/Axiom's deploy fork of `NousResearch/hermes-agent`.

## Branch and remote contract

- Canonical upstream: `upstream/main` (`https://github.com/NousResearch/hermes-agent.git`).
- Axiom deploy branch: `origin/axiom` (`https://github.com/Codename-11/hermes-agent.git`).
- Axiom-Desktop install path: `%LOCALAPPDATA%\hermes\hermes-agent`.
- Axiom-Desktop tracks `origin/axiom`; do not silently switch it back to upstream `main`.
- Axiom-Desktop's update branch must be explicit. On Windows it is persisted in
  `%APPDATA%\Hermes\updates.json`:

  ```json
  {
    "branch": "axiom"
  }
  ```

  The checkout's current branch does not replace this setting; a checkout can
  be on `axiom` while Desktop still checks another configured update channel.
- Bare `hermes update`, `hermes update --check`, and `hermes --version` are intentionally deploy-branch-aware on `axiom`; operators should not need a special Desktop-only update command. On a deploy branch, plain `hermes update` fetches both remotes, reconciles `upstream/main` into `origin/<deploy>` in a temporary worktree, publishes the result, then fast-forwards the live checkout.
- Desktop's update UI should distinguish deploy-branch freshness from upstream disparity: `HEAD..origin/axiom` means a published result is ready to consume, while `origin/axiom..upstream/main` means the next update must first reconcile and publish upstream work.
- If upstream has new commits but `origin/axiom` has not moved, the first host to run `hermes update` becomes the integration host for that run. It resolves and publishes once; later hosts consume the published `origin/axiom` result unless upstream advances again.

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
7. Keep fork-only code that has clean boundaries in **fork-owned modules**, not inline in upstream hotspot files. The deploy-branch update flow lives in `hermes_cli/axiom_update.py` (extracted from `main.py` on 2026-06-21) with a thin import seam back into `main.py`. Upstream never edits a filename it does not ship, so these carry with ~zero merge surface. See FORK.md → "Fork footprint reduction" for the seam contract and the lazy-import rule that avoids the circular import. When adding new fork-only update/deploy logic, put it in `axiom_update.py`, not back in `main.py`.
8. Plain `hermes update` autonomously resolves deploy handoffs when needed. It may run a Hermes resolver in the retained temp worktree, validate no unmerged files/conflict markers remain, run matched focused checks, commit/push `HEAD:<deploy>`, fast-forward the live checkout, and then continue the normal install/restart phase. It must hard-stop on sensitive paths or ambiguous git state.
9. A retained handoff is a snapshot, not a permanent merge state. Before launching the resolver, compare the marker's recorded `origin_head` and `upstream_head` against current `origin/<deploy>`. If both recorded refs are already ancestors, clear the stale marker/worktree and start a fresh deploy update; do not compare completion only against the moving current `upstream/main`. The resolver transcript streams live under an explicit advisory banner, while the parent updater remains authoritative: it validates the worktree, runs focused checks, commits, and pushes only after the child exits. Resolver failures also print the exit code plus a bounded transcript tail.
10. There are no deploy update modes. The first host to observe upstream work publishes the reconciled artifact; any later host fast-forwards to that same `origin/<deploy>` result.

## Desktop Update Control contract

The bundled **Update Control** plugin is a read-only cockpit over the existing
Desktop updater. It reports the local Desktop client and the active backend as
separate targets because they can update on different hosts and schedules.

- **Client freshness** describes the Windows checkout, configured Desktop update
  branch, built Desktop artifact, and running `Hermes.exe`.
- **Backend freshness** describes the connected `hermes serve` runtime. A current
  client does not prove a remote backend is current, and vice versa.
- **Checkout disparity** compares the local `HEAD` with the published deploy
  branch (`origin/axiom`).
- **Deploy disparity** compares the published deploy branch with
  `upstream/main`. Upstream work can be pending reconciliation even when the
  local checkout has consumed every published Axiom commit.

The fork-local `host.updates` plugin facade exposes only detached client/backend
status snapshots and one entry point that opens the native updater for the
active connection target. It does not expose checks, branch selection, progress
streams, raw Electron IPC, shell commands, or apply controls. It reports
Git/update readiness but does not replace the separate build-stamp/source-hash/
running-executable verification below. All polling, mutation, confirmation,
dirty-tree policy, deploy reconciliation, and restart/relaunch handling stay in
the core updater.

Suggested focused verification for Desktop patch work:

```bash
cd apps/desktop
npm run typecheck
NODE_ENV=test npm run test:ui -- \
  src/app/right-sidebar/files/use-project-tree.test.ts \
  src/app/session/hooks/use-prompt-actions.test.tsx \
  src/store/session.test.ts
```

Run React/Vitest slices with `NODE_ENV=test`. A production ambient env can make Testing Library resolve React's production build and fail with `TypeError: React.act is not a function`, even when the Desktop code is fine. If Vitest's jsdom localStorage shim fails in `src/store/session.test.ts`, rerun with the repo's `npm run test:ui -- ...` harness and record the exact failure. Do not treat unrelated harness failures as proof that the Desktop patch is broken.

## Windows HUD stability carry

Axiom carries a narrow Windows HUD stabilization layer while upstream's
cross-platform HUD behavior remains incomplete. It protects four related
boundaries:

1. The transparent frameless HUD is native non-resizable on Windows. Windows
   exposes invisible resize zones on that window type and can grow the window
   during drag movement, especially under display scaling.
2. Drag movement reapplies a size captured when the HUD was created rather than
   using `setPosition()` against geometry Windows may mutate mid-drag.
3. Windows joins Linux's native cursor feed. Electron's
   `setIgnoreMouseEvents(true, { forward: true })` can leave a Windows HUD with
   `WS_EX_TRANSPARENT` active and never deliver the page mousemove required to
   make the composer interactive again.
4. Programmatic HUD close, profile respawn, and app quit remove only the HUD
   restore/broadcast handler. Resource cleanup listeners remain attached, so
   native cursor polling stops instead of leaking a 60 ms timer per HUD cycle.

On Windows, persisted integer geometry is accepted only inside a compact
two-times-default envelope (`620x320` default, `380x160` minimum). Larger
values are treated as drag-growth damage and ignored, so an existing
oversized `hud-state.json` automatically returns to default geometry after
rebuilding and reopening HUD mode. The file is stored at `%APPDATA%\Hermes\hud-state.json`.

Primary seams:

- `apps/desktop/electron/hud-window-geometry.ts`
- `apps/desktop/electron/hud-cursor.ts`
- `apps/desktop/electron/hud-window-lifecycle.ts`
- the HUD creation/movement path in `apps/desktop/electron/main.ts`

Focused verification:

```bash
cd apps/desktop
NODE_ENV=test npm run test:desktop:platforms -- --run \
  electron/hud-cursor.test.ts \
  electron/hud-window-geometry.test.ts \
  electron/hud-window-lifecycle.test.ts \
  electron/hud-url.test.ts
NODE_ENV=test npm run test:ui -- --run \
  src/app/hud/click-through.test.ts \
  src/store/hud.test.ts
npm run typecheck
```

Drop this carry only after upstream provides equivalent or better Windows
behavior for **both** geometry stability and click-through recovery. A resize
handle alone is not equivalent. Review upstream PR #82455 and successors during
integration, then remove the fork modules/tests only after a Windows packaged
build passes physical drag, composer-hover, click, and exit-HUD smoke tests.

## Current Desktop remote-file/access layer

Axiom-Desktop often runs the Electron shell on Windows while the active Hermes gateway/profile is remote. That means a path like `/home/.../artifact.png` usually exists on the gateway, **not** on the Windows client.

Keep this section as the tracking source for remote file/sidebar/artifact behavior. The rule is: prefer upstream-merged fixes, carry only narrow/drop-ready patches on `axiom`, and do not revive older workaround commits unless Bailey explicitly asks for a new local behavior.

### Current state at a glance

| Surface | Current behavior | Source | Notes |
| --- | --- | --- | --- |
| Files/sidebar browse + preview | Remote Desktop sessions use authenticated `/api/fs/*` backend REST for listing, capped text preview, capped data URLs, git-root detection, backend default cwd, and the explicit-submit-only project-directory create carry. Local Desktop sessions keep Electron filesystem IPC. | Upstream #44326 + Axiom hybrid Projects carry | Browsing/typing stays read-only; only Create Project submit may call `ensure-directory`. |
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
| Hybrid Projects overview + typed-path creation + source parity | Keeps Projects primary with a separate flat Recent Sessions lane, removes nested previews, hides global recents in drill-in, labels lane paging, makes Home an explicit active/no-folder creation target, and creates typed missing directories only on explicit submit through local Electron or authenticated/profile-routed remote REST. Projects/Home admit only interactive local conversation sources; Messaging and automation/system runs remain in their appropriate history/search surfaces. | Drop only when upstream covers the complete UI, local/remote mutation contract, and source-filter parity without database cleanup or title heuristics; see `FORK.md` for focused tests and exact criteria. |

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
Get-Content "$env:APPDATA\Hermes\updates.json"
git status --short --branch
git log -5 --oneline
hermes --version
Get-Content "$env:LOCALAPPDATA\hermes\desktop-build-stamp.json"
Get-Item apps\desktop\release\win-unpacked\Hermes.exe | Select-Object FullName,Length,LastWriteTime
Get-CimInstance Win32_Process -Filter "Name = 'Hermes.exe'" | Select-Object ExecutablePath,CreationDate
```

Do not use the executable timestamp alone as proof. Verify all of the following:

1. `%APPDATA%\Hermes\updates.json` names `axiom` explicitly.
2. `HEAD...origin/axiom` shows no unpublished deploy commits waiting for this
   checkout, while `origin/axiom...upstream/main` is interpreted separately as
   fork carry versus upstream work still awaiting reconciliation.
3. `%LOCALAPPDATA%\hermes\desktop-build-stamp.json` exists and its
   `contentHash` matches the current Desktop source hash. A matching Git commit
   alone is insufficient when tracked source is modified.
4. The expected unpacked executable exists and was produced by that verified
   build.
5. the running `Hermes.exe` process points to that executable and started after
   it was built. Otherwise the source/build may be current while the open client
   is still the previous process.
