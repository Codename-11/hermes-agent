# Desktop Staged Updates and Update Control Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Let Hermes Desktop prepare a verified update while the app remains usable, then finish the update through a safe detached restart handoff, with Update Control owning check, stage, restart/apply, progress, release details, and update history.

**Architecture:** Electron remains the machine authority. A new core staging service prepares an exact target in an isolated Git worktree, warms dependency caches, builds the packaged Desktop artifact away from the locked live release, and writes an atomic manifest under `HERMES_HOME`. The existing detached PowerShell handoff validates and consumes that manifest after Electron exits; it never mutates the live venv or release while Desktop is running. The renderer store exposes narrow core operations through the Desktop plugin SDK so Update Control can own the experience without importing raw Electron APIs.

**Tech Stack:** Electron/TypeScript IPC, React + plugin SDK, Nanostores/React Query, PowerShell 5.1, Git worktrees, existing Hermes CLI update flow, Vitest, pytest, and Windows handoff smoke tests.

---

## Product Contract

### User-visible states

1. **Current** — no deploy update is available.
2. **Available** — exact target and categorized changes are visible.
3. **Preparing** — Desktop stays usable while the isolated stage is fetched, dependencies are cached, and the Desktop package is built.
4. **Ready to restart** — the manifest and artifact validate against the current live HEAD and requested target.
5. **Applying** — detached handoff owns the restart and live mutation.
6. **Completed** — relaunched Desktop shows the applied range and history entry.
7. **Invalidated** — target moved, live HEAD/branch changed, stage files were modified, or checkout dirt changed; prepare again.
8. **Failed** — stage or apply failed with a durable log/result and retry/discard affordance.

### Safety invariants

- Never mutate the live checkout, live venv, or live `release/` tree during preparation.
- Never use `--force-venv` to make preparation work.
- Every stage pins `baseSha`, `targetSha`, branch, install root, artifact path, creation time, and content hashes.
- Apply fails closed if live HEAD/branch/install root no longer match the manifest assumptions.
- Dirty live checkout policy remains core-owned and explicit; the plugin does not bypass it.
- The detached handoff keeps the existing Desktop-exit and venv-shim-unlock gates.
- The old one-click immediate handoff remains a compatibility fallback for unstaged updates and old checkouts.
- Update history is append-only, bounded, machine-owned, and contains no credentials or arbitrary command output.
- The plugin receives typed operations, snapshots, progress, and history; it never gets a raw shell/IPC bridge.

## Deliberate Cut Line

Wave 1 does **not** make the Python venv atomically swappable. Windows venvs are path-bound and the current launcher points directly into one install root. Preparation warms the package cache in an isolated environment; restart still performs the authoritative live venv sync, but avoids network/download work in the normal case. Near-instant Python swaps require versioned install roots plus a stable launcher indirection and belong in Wave 2.

---

## Task 1: Define stage, progress, and history contracts

**Objective:** Establish typed data shared by Electron, preload, renderer store, and plugin SDK.

**Files:**
- Create: `apps/desktop/electron/update-stage.ts`
- Create: `apps/desktop/electron/update-stage.test.ts`
- Modify: `apps/desktop/src/global.d.ts`
- Modify: `apps/desktop/electron/preload.ts`
- Modify: `apps/desktop/src/sdk/types.ts`

**Steps:**
1. Add RED tests for manifest validation: exact branch/base/target/install root, valid hashes, stale target, changed live HEAD, missing artifact, and malformed JSON.
2. Define `DesktopUpdateStageManifest`, `DesktopUpdateStageStatus`, `DesktopUpdateHistoryEntry`, and typed stage/discard/restart results.
3. Implement pure parsing/validation and atomic JSON read/write helpers.
4. Add preload operations: `status`, `prepare`, `discard`, `restartAndApply`, `history`, and progress subscription.
5. Run Electron tests and Desktop typecheck.

**Verification:**
```bash
cd apps/desktop
NODE_ENV=test npx vitest run --project electron electron/update-stage.test.ts
NODE_ENV=test npm run typecheck
```

## Task 2: Prepare an isolated target while Desktop remains open

**Objective:** Build a real packaged Desktop artifact and warm dependency caches without touching the live install.

**Files:**
- Create: `scripts/desktop-stage-update.ps1`
- Create: `tests/hermes_cli/test_desktop_stage_update.py`
- Modify: `apps/desktop/electron/update-stage.ts`
- Modify: `apps/desktop/electron/main.ts`
- Modify: `apps/desktop/electron/updater-process.ts`
- Modify: `apps/desktop/electron/updater-process.test.ts`

**Steps:**
1. Add RED tests for command construction, one-stage-at-a-time locking, idempotent same-target prepare, superseding a stale target, and cleanup.
2. Stage under `HERMES_HOME/update-stage/desktop/` using an isolated Git worktree pinned to `origin/<branch>`.
3. Run deterministic dependency preparation in the stage worktree. Populate normal npm/uv caches; do not reuse or mutate the live venv.
4. Run the packaged Desktop build in the stage worktree and verify the Windows executable with the existing PE launchability gate or equivalent shared validator.
5. Hash the manifest and required artifact files, then atomically publish `stage.json` only after every check passes.
6. Stream structured progress into Electron and retain a bounded preparation log.
7. Remove failed partial worktrees/artifacts while retaining the human-readable failure record.

**Verification:**
```bash
cd apps/desktop
NODE_ENV=test npx vitest run --project electron electron/update-stage.test.ts electron/updater-process.test.ts
cd ../..
python -m pytest -q -o addopts='' tests/hermes_cli/test_desktop_stage_update.py
```

## Task 3: Consume a verified stage through the detached restart handoff

**Objective:** Finish the update only after Electron exits, reusing the staged target and package.

**Files:**
- Modify: `scripts/desktop-update.ps1`
- Modify: `apps/desktop/electron/main.ts`
- Modify: `apps/desktop/electron/update-marker.ts`
- Modify: `apps/desktop/electron/update-marker.test.ts`
- Modify: `tests/hermes_cli/test_desktop_stage_update.py`

**Steps:**
1. Add `-StageManifest <path>` to the repo-owned handoff.
2. Keep the existing marker claim, 30-second Desktop exit gate, and venv shim unlock gate unchanged.
3. Revalidate manifest, live branch, live HEAD, install root, target ancestry, artifact hashes, and dirty-tree policy after Desktop exits.
4. Fast-forward the live checkout to the pinned target using the existing deploy-aware update authority; never silently retarget to a newer moving ref.
5. Sync the live Python environment from warmed cache.
6. Atomically rotate live Desktop release to `.bak`, move/copy the verified staged package into place, and validate the new executable before deleting rollback.
7. On package validation failure, restore `.bak`, write failure history/result, and relaunch the prior Desktop.
8. On success, write update brief/history, clean the consumed stage/worktree, remove the marker if owned, and relaunch detached.
9. Preserve immediate unstaged handoff as fallback.

**Verification:**
- Pester/PowerShell harness or pytest subprocess coverage for success, stale stage, locked venv, bad hash, failed executable validation, rollback, and idempotent consumed manifest.
- Existing updater tests remain green.

## Task 4: Add bounded update history and structured change details

**Objective:** Reuse the existing update brief pipeline as structured machine data instead of maintaining a second changelog system.

**Files:**
- Modify: `hermes_cli/update_ui.py`
- Modify: `tests/hermes_cli/test_update_ui.py`
- Create: `apps/desktop/electron/update-history.ts`
- Create: `apps/desktop/electron/update-history.test.ts`
- Modify: `apps/desktop/electron/main.ts`
- Modify: `apps/desktop/src/global.d.ts`

**Steps:**
1. Extend successful update brief generation to write a sidecar JSON record containing range, branch, timestamps, commit categories, subjects/authors/SHAs, shortstat, files changed, result, and brief path.
2. Record failed/cancelled stage/apply attempts with phase, message, and log path but no secrets/raw environment.
3. Keep the most recent 50 entries and retain the existing Markdown archive/mirror.
4. Add Electron readers for pending categorized changes and historical entries; reject paths outside Hermes-owned log/stage roots.
5. Test malformed entries, ordering, cap, missing Markdown, and legacy brief-only installations.

## Task 5: Expose safe update operations through the plugin SDK

**Objective:** Let Update Control own the lifecycle without exposing raw Electron mutation doors to arbitrary plugins.

**Files:**
- Modify: `apps/desktop/src/store/updates.ts`
- Modify: `apps/desktop/src/store/updates.test.ts`
- Modify: `apps/desktop/src/sdk/types.ts`
- Modify: `apps/desktop/src/sdk/index.ts`
- Modify: `apps/desktop/src/sdk/index.test.ts`
- Modify: `website/docs/developer-guide/desktop-plugin-sdk.md`

**Steps:**
1. Add core store operations for refresh, prepare, discard, restart/apply, stage status, history, and progress ingestion.
2. Expose only named typed operations in `host.updates`; do not expose branch mutation, arbitrary options, shell commands, or the raw bridge.
3. Make methods async-safe and return detached immutable snapshots.
4. Update SDK tests to assert the exact narrow surface and prove mutation stays core-authorized.
5. Document lifecycle, unsupported platforms, and fallback behavior.

**Proposed SDK surface:**
```ts
host.updates.getStatus(target)
host.updates.getStage()
host.updates.getHistory()
host.updates.refresh(target)
host.updates.prepare()
host.updates.discardStage()
host.updates.restartAndApply()
host.updates.openNative()
```

## Task 6: Rebuild Update Control as the primary update experience

**Objective:** Replace the read-only two-card dashboard with a clean release/update console.

**Files:**
- Modify: `apps/desktop/src/plugins/update-control/plugin.tsx`
- Modify: `apps/desktop/src/plugins/update-control/model.ts`
- Modify: `apps/desktop/src/plugins/update-control/model.test.ts`
- Modify: `apps/desktop/src/plugins/update-control/plugin.test.tsx`
- Create: `apps/desktop/src/plugins/update-control/pending-changes.tsx`
- Create: `apps/desktop/src/plugins/update-control/history.tsx`
- Create: `apps/desktop/src/plugins/update-control/update-actions.tsx`

**Interface:**
- Header: current → target, branch, stage badge, refresh.
- Primary action card: `Prepare update`, live phase/progress/log summary, `Restart and finish`, `Discard`.
- Pending changes: category chips (Features, Fixes, Performance, Refactors, Docs, Other), expandable commits, author/date/SHA, diff stat, changed-file count.
- Backend/client selector: preserve remote backend update distinction.
- History tab: completed/failed entries, range, duration, result, expandable categorized changes, open brief/log.
- Diagnostics drawer: dirty state, unsupported install method, stage invalidation reason, exact fallback command.
- Native updater becomes compatibility fallback, not the primary button.

**UX rules:**
- No second modal over the pane for normal flow.
- Stage progress does not block chat or other panes.
- `Restart and finish` explicitly says the app will close and reopen.
- Failure is terminal and closeable; no immortal spinner.
- Use SDK components/theme variables only.

## Task 7: Recovery, startup reconciliation, and compatibility

**Objective:** Make interrupted preparation/application recover predictably.

**Files:**
- Modify: `apps/desktop/electron/main.ts`
- Modify: `apps/desktop/electron/update-marker.ts`
- Modify: `apps/desktop/electron/update-result.ts`
- Modify adjacent tests.

**Steps:**
1. On boot, distinguish active apply marker, abandoned preparation lock, ready stage, consumed stage, and failed result.
2. Do not park boot for preparation-only state.
3. Continue parking boot for an active detached apply owner.
4. Surface consumed result exactly once and retain the history entry.
5. Clean stale temporary worktrees only after proving no live owner and preserving logs.
6. Keep old staged Tauri updater and immediate repo-script handoff fallback for checkouts predating the new manifest.

## Task 8: End-to-end validation and rollout

**Objective:** Prove both source behavior and the actual Windows lifecycle.

**Automated gates:**
```bash
cd apps/desktop
NODE_ENV=test npm run typecheck
NODE_ENV=test npm run test:ui -- --run
npm run build
NODE_ENV=test npx vitest run --project electron \
  electron/update-stage.test.ts \
  electron/update-history.test.ts \
  electron/update-marker.test.ts \
  electron/updater-process.test.ts
cd ../..
python -m pytest -q -o addopts='' \
  tests/hermes_cli/test_desktop_stage_update.py \
  tests/hermes_cli/test_update_ui.py \
  tests/hermes_cli/test_update_concurrent_quarantine.py
```

**Windows runtime acceptance:**
1. Run Desktop at commit A and publish commit B to `origin/axiom`.
2. In Update Control, refresh and inspect categorized B changes.
3. Prepare while continuing to chat and switch profiles.
4. Verify live checkout/venv/release remain at A during preparation.
5. Verify stage manifest/artifact pin B and `Ready to restart` survives pane close/reopen and app restart.
6. Click `Restart and finish`.
7. Verify Desktop exits, handoff applies B, prior release rollback exists until executable validation passes, Desktop relaunches, and relay reconnects.
8. Verify live HEAD/build stamp/executable all report B.
9. Verify history includes A..B and the existing update brief.
10. Repeat with corrupted artifact and changed live HEAD; both must fail closed and retain/relaunch A.

## Wave 2: Versioned runtime roots (deferred)

Only pursue after Wave 1 ships and restart timing is measured. Introduce a stable launcher that selects `installs/<sha>/`, build complete Python/Desktop roots side-by-side, atomically swap a `current` pointer, retain N rollback roots, and garbage-collect inactive roots. This can make restart nearly instant, but it changes bootstrap, aliases, services, shortcuts, plugin paths, and uninstall semantics; it should not be smuggled into the first staged-update patch.

---

## Acceptance Criteria

- Desktop remains fully usable throughout preparation.
- No live install file is mutated before restart.
- Prepared target and commit details are visible in Update Control.
- Restart consumes only the verified pinned target or fails closed.
- A failed package/apply relaunches the prior working Desktop.
- Update Control can check, prepare, discard, restart/apply, show progress/result, and browse history without opening the legacy native updater.
- Existing immediate update and old-checkout fallback paths still work.
- Full Desktop tests/build and focused Python/Electron update tests pass.
