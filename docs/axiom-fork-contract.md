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
- Bare `hermes update`, `hermes update --check`, and `hermes --version` remain deploy-branch-aware on `axiom`, but operational policy separates reconciliation from deployment. When `upstream/main` is not already contained in `origin/axiom`, bare update queues detached carry replay to `origin/axiom-next` and returns without runtime mutation. A later explicit bare update may promote/deploy only an exact ready report through rollback archival, expected-SHA leases, ref read-back, and the normal updater lifecycle.
- Desktop's update UI should distinguish deploy-branch freshness from upstream disparity: `HEAD..origin/axiom` means a published result is ready to consume, while `origin/axiom..upstream/main` means reconciliation is required—not that the live host should merge it now.
- No unattended host or ordinary audit run may publish `origin/axiom` merely because it is the first to observe upstream movement. Candidate publication, deploy promotion, and live deployment are separate approvals.

## Status and source-of-truth contract

Use these surfaces deliberately; do not duplicate live branch counts by hand:

| Surface | Purpose |
| --- | --- |
| `docs/axiom-fork-contract.md` | Canonical concise branch/Desktop/update contract. |
| `fork-carries.json` + `fork-carries.schema.json` | Canonical ordered carry manifest and schema for IDs, provenance, exact repo paths, focused test paths, declared checks, retirement criteria, and optional immutable replay metadata. Carries without replay metadata remain declaration-only/incomplete. |
| `FORK.md` | Detailed prose carry contract, retired fork surface, historical fork-only commit classification, and validation expectations. |
| `scripts/fork-status.py` | Read-only live status report for branch divergence, dirty files, Sentinel sync state, and optional Axiom-Desktop SSH branch check. |
| `python scripts/fork_carry_manifest.py validate --json` | Read-only manifest validation; checks declarations only and never executes carry checks. |
| `python scripts/fork_carry_manifest.py status --json` | Read-only manifest inventory/status report; never executes carry checks. |
| `python scripts/fork_carry_replay.py plan --json` | Deterministic local report separating replay-ready carries from incomplete active declarations; performs no Git mutation or checks. |
| `python scripts/fork_carry_replay.py probe --carry desktop-profile-gateway-activation --json` | Local disposable-worktree applicability probe for the first extracted carry. Checks are opt-in with `--run-checks`; no fetch, push, updater, or live-worktree checkout. |
| `docs/refs/axiom-fork-reconciliation-standard.md` | Mandatory isolated candidate, upstream-survival, caller-contract, promotion, and deployment boundary. |
| Obsidian `3. System/Operations/Runbooks/Hermes Axiom Sync Runbook.md` | Operational procedure for upstream integration and live deployment separation. |
| `~/SYSTEM.md` / Hermes Overview | Quick-start pointers only; keep them short and point back here/runbook. |

Before updating, resolving conflicts, or checking whether Docker-Server and Axiom-Desktop are aligned, run:

```bash
cd ~/.hermes/hermes-agent
scripts/fork-status.py
scripts/fork-status.py --desktop   # optional; read-only, requires reachable Windows SSH
python scripts/fork_carry_manifest.py validate --json
python scripts/fork_carry_manifest.py status --json
```

The manifest commands are read-only inventory surfaces. They validate/report declarations only and do **not** execute any carry checks listed in `fork-carries.json`.

## Update contract

The `axiom` branch is expected to:

1. Pull/merge upstream `main` regularly.
2. Carry a small, documented patch layer for Axiom-specific deployment and Desktop stability.
3. Keep fork-only patches narrow and easy to drop when upstream lands equivalent fixes.
4. Run focused Desktop checks before pushing patches that affect Axiom-Desktop.
5. Treat upstream disparity as a reconciliation signal, not permission for a live host to merge or publish. `origin/axiom` freshness controls deployment; `upstream/main...HEAD` tells maintainers when to generate a separate candidate.
6. Do not rely only on merge conflicts to retire or preserve fork patches. Conflicts catch same-line overlap, but upstream can land a better adjacent/architectural fix—or a clean merge can preserve only half of a caller/callee contract. Every refresh must prove non-carry paths equal the pinned upstream tree and audit changed signatures through real callers.
7. Keep fork-only code that has clean boundaries in **fork-owned modules**, not inline in upstream hotspot files. The deploy-branch update flow lives in `hermes_cli/axiom_update.py` (extracted from `main.py` on 2026-06-21) with a thin import seam back into `main.py`. Upstream never edits a filename it does not ship, so these carry with ~zero merge surface. See FORK.md → "Fork footprint reduction" for the seam contract and the lazy-import rule that avoids the circular import. When adding new fork-only update/deploy logic, put it in `axiom_update.py`, not back in `main.py`.
8. The updater retains bounded resolver/handoff machinery for recovery and historical compatibility, but operator policy forbids invoking that machinery to integrate new upstream work from a running deployment. When upstream is pending, stop and generate the isolated candidate defined in `docs/refs/axiom-fork-reconciliation-standard.md`.
9. Parent validation is resumable and self-repairing within a bound. Marker phases retain `resolved_head`, `validation_sha`, and a typed ledger bound to the exact full resolved SHA. Every result records stable check ID, SHA-256 fingerprint of the canonical check spec, status, nullable return code, bounded/redacted output tail when available, duration, and completion time. Retries reuse a passed/unavailable result only when both resolved SHA and fingerprint match; a changed HEAD invalidates all results and a changed spec invalidates that check. Legacy command-keyed `check_status` markers are read safely but rerun into the typed ledger. Checks run serially with Python before Desktop validation. Desktop validation prepares dependencies once inside the retained worktree with `npm ci --include=dev --ignore-scripts`; it never installs test tooling into the live Hermes runtime. A real compile/test failure advances to `repair_pending` and gives the bounded parent diagnostics back to the resolver for at most two tracked-source repair passes in the same updater invocation; each pass creates a new checkpoint and reruns authoritative validation. Dependency-preparation and environment failures remain parent/operator concerns and are not sent to the LLM. Before Windows live fast-forward, discard only generated npm lock churn whose matching manifest is clean. Case-colliding index aliases may be removed only when the target deletes the entire collision group and the physical blob matches one indexed blob; otherwise preserve the file and stop. Surface bounded Git stderr for every remaining fast-forward failure.
10. A retained handoff is a snapshot, not a permanent merge state. Before launching a resolver for a phase-less or `resolve_pending` marker, compare the recorded `origin_head` and `upstream_head` against current `origin/<deploy>`. If both recorded refs are already ancestors, clear the stale marker/worktree and start a fresh deploy update; validation checkpoints must never be discarded by this snapshot rule. Resolver timeouts terminate the process tree and may continue only when structural resolution can be checkpointed safely. `push_pending` retries publish the recorded exact commit without rerunning resolution, and the marker/worktree are removed only after publication (and, for a normal update, live fast-forward) succeeds.
11. Ordinary hosts consume an already-reviewed `origin/<deploy>` artifact. They do not become integration hosts merely because they observe upstream movement.

## Desktop session convergence contract

Concurrent Desktop chats and profile gateways must converge without borrowing
identity or state from whichever chat/profile is currently foregrounded:

- asynchronous retries, transcript/todo hydration, status snapshots, and
  gateway events remain owned by their dispatch-time profile, gateway, stored
  chat, and runtime;
- reconnects reconcile `session.active_list` through the canonical runtime
  cache on the exact primary or secondary gateway that reconnected;
- older snapshots and polls cannot overwrite newer optimistic/streaming state;
- idle, vanished, or reclaimed runtimes fully clear stream/status state and
  private reverse mappings; and
- running/attention dots, gateway retention, and unscoped stream pins are
  profile-qualified, including when cloned profiles share a stored session id.

The detailed protected-file inventory, focused verification commands, upstream
overlap references, and drop conditions live in `FORK.md` under **Axiom Desktop
session convergence and profile-safe live status**.

## Desktop Update Control contract

The opt-in **Axiom Enhancements** disk plugin owns Update Control presentation;
it is distributed from the Axiom Agent Library rather than bundled in Hermes
core. It reports the local Desktop client and the active backend as separate
targets because they can update on different hosts and schedules.

Update Control is a singleton `placement: 'main'` pane in the standard Desktop
tab strip, not a permanent workspace route. Closing its tab dismisses only the
pane; it must not disable the plugin or remove its sidebar, status, or palette
entry points. Unrelated contribution-registry refreshes must preserve that
dismissal. Reopening from any entry point restores and focuses the same pane
through the plugin-scoped `ctx.panes.reveal(...)` SDK action.

The Desktop sidebar also exposes the existing singleton in-app Browser. Its row
re-fronts the current Browser tab without changing its URL, or opens the Browser
home page when no URL tab exists; it must not create a parallel browser surface.

- **Client freshness** describes the Windows checkout, configured Desktop update
  branch, built Desktop artifact, and running `Hermes.exe`.
- **Backend freshness** describes the connected `hermes serve` runtime. A current
  client does not prove a remote backend is current, and vice versa.
- **Checkout disparity** compares the local `HEAD` with the published deploy
  branch (`origin/axiom`).
- **Deploy disparity** compares the published deploy branch with
  `upstream/main`. Upstream work can be pending reconciliation even when the
  local checkout has consumed every published Axiom commit.

The fork-local typed `host.updates` facade exposes detached client/backend
snapshots, backend apply progress, staged-update status/history, refresh, stage
preparation/cancellation/discard, guarded client or backend apply, restart/apply,
and upstream reconciliation status. The renderer may present and request those
operations, but core remains authoritative for checks, branch selection,
progress collection, locks, confirmation, dirty-tree policy, Git/filesystem
mutation, process shutdown, rollback, deploy reconciliation, and relaunch. The
facade never exposes raw Electron IPC, shell commands, repository paths, or
updater internals. Its readiness display does not replace the separate
build-stamp/source-hash/running-executable verification below.

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
| Hybrid Projects overview + typed-path creation + source parity | Keeps Projects primary with a separate flat Recent Sessions lane; adds persisted five-session Project/Home disclosure previews with active-session inclusion and labeled full drill-in; computes ownership from a complete compact query; hides global recents in drill-in; labels Project/Home identity and reassignment; and creates typed missing directories only on explicit submit through local Electron or authenticated/profile-routed remote REST. Projects/Home admit only interactive local conversation sources. | Drop only when upstream covers the complete UI, membership, local/remote mutation, and source-filter contracts without database cleanup or title heuristics; see `FORK.md` for exact criteria. |
| Project/worktree session lifecycle | Separates main-checkout sessions, new isolated-worktree sessions, current-session worktree retargeting, existing-worktree entry, and return-to-Project checkout while reusing the existing Git facade/dialog/store. | Drop when upstream provides the same separate flows, non-Git gating, and visible failures through its existing worktree primitives. |
| Rebindable Desktop voice controls | Keeps dictation, Read replies aloud, and Hey Hermes as independent unbound Desktop actions with active-composer routing, profile-authoritative auto-TTS persistence, gateway-authoritative wake-word behavior, visible errors, tooltips, and `aria-keyshortcuts`. | Drop when upstream provides equivalent independent rebindable actions with the same routing, persistence, wake ownership, and accessibility behavior. |

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
