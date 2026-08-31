# Hermes Agent — Axiom Dev Log

## 2026-08-30 — Restore fork-aware `hermes update --check`

- Repaired a semantic carry drop from the Axiom regeneration: the banner still counted `HEAD..origin/axiom` plus `origin/axiom..upstream/main`, but the interactive CLI had reverted to origin-only checks.
- Deploy-branch checks now fetch and print both lanes explicitly, including zero counts, so a current local install cannot hide upstream work awaiting reconciliation.
- Bare update commands once again infer checked-out `axiom`/`tgi` as their target; ordinary feature branches retain the upstream default to `main`.
- Added CLI regression coverage for the exact failure mode: local equals `origin/axiom` while upstream has pending commits.

## 2026-08-30 — Restore thin Update Control facade

- Restored a versioned `host.updates` plugin capability after the 2026-08-22 regeneration accidentally preserved the external Axiom Enhancements UI but omitted its core connector.
- Kept Desktop's normal deploy-aware check/apply/rebuild/relaunch path authoritative; did not restore the retired parallel staging/history engine.
- Added background `sync-upstream` execution and live output around the existing `hermes_cli.axiom_update` LLM worktree resolver so reconciliation can run while Desktop remains open.
- Registered `desktop-update-control-facade` as a replayable carry with an immutable source commit, focused Electron/SDK tests, isolated dependency preparation, full typecheck, and a successful replay probe.

## 2026-08-30 — Retire Axiom ownership of Desktop registered-source routing

- Refreshed `axiom` from current `upstream/main` without Desktop routing conflicts.
- Confirmed the former registered-source routing files have converged with upstream and removed their stale protected paths, deleted test references, and checks from the retired carry declaration.
- Kept upstream multi-gateway/profile-qualified behavior intact while narrowing Axiom's deployment policy: Docker-Server is canonical, and the Windows-local backend is an explicit local/offline fallback rather than a remote-profile mirror.
- Preserved the only active Desktop carry: gateway-scoped hybrid Projects with a separate Recent Sessions lane.

## 2026-08-23 — Standardize non-deploying fork reconciliation

- Local and remote Desktop gateways failed after a final upstream refresh preserved `gateway_ws(auth_identity=..., subprotocol=...)` but retained a stale `handle_ws(ws)` implementation.
- The 2026-08-22 generated candidate architecture remains valid; the missing controls were exact upstream survival for non-carry paths and route-level caller-to-real-implementation verification after every final refresh.
- Reconciliation, deploy-ref promotion, and live deployment are separate authorization states. Audit work may publish a candidate ref but must not move `origin/axiom`, edit live checkouts, install Desktop, or restart services.
- Bare `hermes update` now consumes any already-reviewed deploy artifact, otherwise queues a detached exact carry replay to `origin/<deploy>-next` and returns before install/restart. A later explicit invocation validates the complete exact-SHA report, publishes/read-backs a rollback archive, lease-promotes the candidate, realigns the stashed checkout, and resumes the normal update lifecycle.
- The generated worker enforces active replay readiness, carry-owned deltas, generated-from-pinned-upstream survival, deduplicated declared checks, a final upstream refresh, candidate-ref lease publication, and exact read-back. It never pushes the deploy ref.
- Canonical procedure: `docs/refs/axiom-fork-reconciliation-standard.md`; operational source of truth: Obsidian `3. System/Operations/Runbooks/Hermes Axiom Sync Runbook.md`.

## 2026-08-22 — Regenerate Axiom from current upstream carry stack

- Base: `upstream/main` at `987064caa4f8845f605ac7346fed5b72fddfb21c`.
- Rollback: `origin/archive/axiom-pre-regeneration-20260822` at `d80816d200974e20702364ddd4426e97c6a2399e`.
- Replaced historical whole-branch merge ancestry with 17 bounded, immutable carries, then refreshed the candidate through upstream `530028c213`.
- Retired broad legacy Desktop profile/session, staged updater UI, OAuth/media, HUD/theme/window, voice/terminal, and project-lifecycle snapshots in favor of current upstream.
- Preserved gateway-scoped hybrid Projects, registered-source profile/session routing, Forge, project source policy, PTY profile tokens, MCP OAuth locking, Windows portability, webhook route toolsets, dashboard plugin auth, routed proxy providers, cron profile ownership, Lucid, Buzz mention policy, TUI plugin cards, Discord bot admission, and deploy-branch update reconciliation.
- `fork-carries.json` validates with 17 replay-ready carries and zero declaration-only active carries.
- Candidate verification: manifest/replay tooling 111 passed; initial 16/16 and final 17/17 clean carry probes; focused Python integration 764 passed / 10 skipped plus updater 170 passed / 1 skipped; Desktop typecheck and package build passed; focused Projects 44/44; PTY 2/2; full Desktop 6,980 passed with all 45 broad failures classified against upstream/order-isolation controls.
- Operator live smoke passed for local/remote project isolation, registered-source session navigation, and Axiom Enhancements. The verified candidate `2c337df3aa` was promoted to `origin/axiom`; `origin/axiom-next` remains at the same tested commit and the pre-regeneration rollback ref remains frozen.
- Final delta gates: registered-source routing 30/30 Node and 71/71 Vitest with full Desktop typecheck; Forge 43/43; upstream refresh 62 passed/16 skipped gateway-webhook, 131 passed/1 skipped updater, and 4/4 version preview; Axiom Enhancements 0.5.0 contract smoke and 37/37 tests.
