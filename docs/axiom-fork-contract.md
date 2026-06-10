# Axiom Hermes Fork Contract

This repository is Bailey/Axiom's deploy fork of `NousResearch/hermes-agent`.

## Branch and remote contract

- Canonical upstream: `upstream/main` (`https://github.com/NousResearch/hermes-agent.git`).
- Axiom deploy branch: `origin/axiom` (`https://github.com/Codename-11/hermes-agent.git`).
- Axiom-Desktop install path: `%LOCALAPPDATA%\hermes\hermes-agent`.
- Axiom-Desktop tracks `origin/axiom`; do not silently switch it back to upstream `main`.
- Bare `hermes update` is intentionally supported on `axiom`; the Desktop update prompt should keep showing `hermes update` for this deploy branch.
- Desktop's update UI should distinguish deploy-branch freshness from upstream disparity: it checks `HEAD..origin/axiom` for update availability and also surfaces `upstream/main` ahead/behind counts so Axiom can see carried fork delta without treating it as an update blocker.

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
npm run type-check
npm exec -- vitest run --environment jsdom \
  src/app/right-sidebar/files/use-project-tree.test.ts \
  src/app/session/hooks/use-prompt-actions.test.tsx \
  src/store/session.test.ts
```

If Vitest's jsdom localStorage shim fails in `src/store/session.test.ts`, rerun with the repo's `npm run test:ui -- ...` harness and record the exact failure. Do not treat unrelated harness failures as proof that the Desktop patch is broken.

## Current Desktop file-browser patch layer

These patches are carried because Axiom-Desktop commonly connects to a remote Hermes gateway while the Electron shell runs on Windows:

- Upstream PR #42497: `fix(desktop): skip remote session cwd in Files panel to prevent ENOENT`
  - Remote gateway cwd paths such as `/home/...` are not local Windows paths.
  - The Files pane shows local browsing semantics instead of trying to `fs.readdir()` the remote cwd locally.
- Upstream PR #43082: `fix desktop remote cwd workspace drift`
  - Runtime `session.info.cwd` from a remote gateway must not overwrite Desktop's local workspace cwd.
  - Keeps local workspace selection stable while remote chat/tool runtime reports its own cwd.
- Upstream PR #42603: `fix(desktop): narrow hover-reveal trigger strip to avoid blocking scrollbar`
  - Prevents collapsed side-panel hover activation from stealing the scrollbar hit area.
- Upstream PR #41189, focused manual port: `fix(desktop): raise FILE_BROWSER_MAX_WIDTH from 20rem to 40rem`
  - The PR head is a merge commit with unrelated upstream changes; carry only the `FILE_BROWSER_MAX_WIDTH` change.

## Deliberately not carried yet

- Upstream PR #40090 (`remote file browser via dashboard API`) is not carried yet.
  - It exposes remote filesystem directory listing through the dashboard API and changes the security/architecture model.
  - Revisit only if Axiom explicitly wants Desktop to browse the remote server filesystem.
- Upstream PR #39122 (`remote workspace + terminal over SSH`) is not carried yet.
  - It is a broad architecture change touching Electron SSH, terminal routing, settings, and file browsing.
  - Evaluate in a dedicated branch before considering it for `axiom`.

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
