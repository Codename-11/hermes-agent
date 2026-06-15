# Axiom Hermes Fork Contract

This repository is Bailey/Axiom's deploy fork of `NousResearch/hermes-agent`.

## Branch and remote contract

- Canonical upstream: `upstream/main` (`https://github.com/NousResearch/hermes-agent.git`).
- Axiom deploy branch: `origin/axiom` (`https://github.com/Codename-11/hermes-agent.git`).
- Axiom-Desktop install path: `%LOCALAPPDATA%\hermes\hermes-agent`.
- Axiom-Desktop tracks `origin/axiom`; do not silently switch it back to upstream `main`.
- Bare `hermes update`, `hermes update --check`, and `hermes --version` are intentionally deploy-branch-aware on `axiom`; operators should not need a special Desktop-only update command.
- Desktop's update UI should distinguish deploy-branch freshness from upstream disparity: it checks `HEAD..origin/axiom` for update availability and also surfaces `upstream/main` ahead/behind counts so Axiom can see carried fork delta without treating it as an update blocker.
- If upstream has new commits but `origin/axiom` has not moved, Desktop's **client** update UI may show upstream disparity, but it should not present that as an installable Desktop-client update.
- Desktop's **backend** update UI is different: it should prompt when `hermes update` would do useful work, including upstream commits not yet merged into `origin/axiom`. Show the count breakdown (`HEAD..origin/axiom` plus `origin/axiom..upstream/main`) so the operator sees why the backend update is actionable.

## Update contract

The `axiom` branch is expected to:

1. Pull/merge upstream `main` regularly.
2. Carry a small, documented patch layer for Axiom-specific deployment and Desktop stability.
3. Keep fork-only patches narrow and easy to drop when upstream lands equivalent fixes.
4. Run focused Desktop checks before pushing patches that affect Axiom-Desktop.
5. Treat upstream disparity as a maintenance signal, not a deploy blocker. `origin/axiom` freshness controls Axiom-Desktop updates; `upstream/main...HEAD` tells maintainers when to review drift.
6. Do not rely only on merge conflicts to retire fork patches. Conflicts catch same-line overlap, but upstream can land a better adjacent/architectural fix that merges cleanly. During each upstream merge, review the carried Desktop patch layer and drop local fixes only after verifying upstream has an equivalent or better behavior.

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

## Desktop file-browser patch layer

The old Axiom-specific Desktop remote-filesystem workaround layer was retired on 2026-06-12 after upstream shipped native read-only remote filesystem browsing around `969aeb279`. Do not reintroduce the retired patches during upstream sync unless Bailey explicitly asks for a new Axiom-specific behavior.

Retired/superseded:

- `e830ac3e6` — `fix(desktop): skip remote session cwd in Files panel to prevent ENOENT`
- `b6d71f248` — `fix desktop remote cwd workspace drift`
- `8e5b55378` — `docs: clarify local files pane in remote mode`

Still intentionally carried:

- Upstream PR #42603: `fix(desktop): narrow hover-reveal trigger strip to avoid blocking scrollbar`
  - Prevents collapsed side-panel hover activation from stealing the scrollbar hit area.
- Upstream PR #41189, focused manual port: `fix(desktop): raise FILE_BROWSER_MAX_WIDTH from 20rem to 40rem`
  - The PR head was a merge commit with unrelated upstream changes; carry only the `FILE_BROWSER_MAX_WIDTH` change.

Broad remote workspace / terminal-over-SSH work remains a dedicated-branch evaluation item, not something to merge casually into `axiom`.

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
