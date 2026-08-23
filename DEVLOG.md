# Hermes Agent — Axiom Dev Log

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
