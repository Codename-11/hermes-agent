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

## Current Desktop remote-file/access layer

Axiom-Desktop commonly connects to a remote Hermes gateway while the Electron shell runs on Windows. Keep this layer small, documented, and easy to drop when upstream lands equivalent behavior.

### Upstream behavior already merged into `main`

- Upstream PR #44326: `feat(desktop): browse remote backend files (salvage #43434)`
  - Remote Desktop sessions use authenticated read-only `/api/fs/*` backend REST for directory listing, capped text previews, capped data URLs, git-root detection, and backend default cwd.
  - Local Desktop sessions keep Electron filesystem IPC.
  - This supersedes the older local-only ENOENT workaround direction from #42497/#43082 for the main Files/sidebar remote-browsing path.
- Upstream PR #43109: `fix(desktop): stage dropped files into the remote session workspace`
  - OS/Finder drops upload/stage local bytes into the remote session workspace instead of leaking local absolute paths into remote prompts.
- Upstream PR #46658: `fix(desktop): sync $connection on profile switch so remote profiles upload image bytes`
  - Remote-aware surfaces (`image.attach_bytes`, `/api/fs/*`, `/api/media`) follow the active profile/backend after profile switches.

### Retired/superseded Axiom carries

Do not reintroduce these older Axiom-specific remote-filesystem workarounds during upstream sync unless Bailey explicitly asks for a new Axiom-specific behavior:

- `e830ac3e6` — `fix(desktop): skip remote session cwd in Files panel to prevent ENOENT`
- `b6d71f248` — `fix desktop remote cwd workspace drift`
- `8e5b55378` — `docs: clarify local files pane in remote mode`

### Carried on `axiom` until upstream replacement lands

- Upstream PR #44538: `fix(desktop): remote-mode chat file links download via the fs bridge instead of dead file:// URLs`
  - Remote-mode chat/media fallback links fetch bytes through the authenticated `/api/fs/read-data-url` bridge and download locally instead of opening a gateway-host `file://` path on Windows.
  - Drop this carry once upstream merges #44538 or an equivalent/better remote chat-file download path.
- Upstream PR #42603: `fix(desktop): narrow hover-reveal trigger strip to avoid blocking scrollbar`
  - Prevents collapsed side-panel hover activation from stealing the scrollbar hit area.
- Upstream PR #41189, focused manual port: `fix(desktop): raise FILE_BROWSER_MAX_WIDTH from 20rem to 40rem`
  - The PR head was a merge commit with unrelated upstream changes; carry only the `FILE_BROWSER_MAX_WIDTH` change.

### Deliberately not carried yet

- Upstream PR #39122 (`remote workspace + terminal over SSH`) is not carried yet.
  - It is a broad architecture change touching Electron SSH, terminal routing, settings, and file browsing.
  - Evaluate in a dedicated branch before considering it for `axiom`.
- Upstream PR #39183 (`edit files in place from the preview pane`) is not carried yet.
  - It is stacked on #39122 and introduces remote write-back/editing. Do not mix it into the read-only remote browse/preview lane.
- Upstream PR #46663 (`convert file:// URLs to HTTP download for remote gateways`) is not carried.
  - It overlaps #44538 but currently conflicts with `main`/`axiom`; prefer the cleaner fs-bridge chat-file fix unless upstream lands a consolidated replacement.

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
