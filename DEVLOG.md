# Hermes Agent — Axiom Dev Log

## 2026-08-22 — Regenerate Axiom from current upstream carry stack

- Base: `upstream/main` at `987064caa4f8845f605ac7346fed5b72fddfb21c`.
- Rollback: `origin/archive/axiom-pre-regeneration-20260822` at `d80816d200974e20702364ddd4426e97c6a2399e`.
- Replaced historical whole-branch merge ancestry with 16 bounded, immutable carries.
- Retired broad legacy Desktop profile/session, staged updater UI, OAuth/media, HUD/theme/window, voice/terminal, and project-lifecycle snapshots in favor of current upstream.
- Preserved gateway-scoped hybrid Projects, Forge, project source policy, PTY profile tokens, MCP OAuth locking, Windows portability, webhook route toolsets, dashboard plugin auth, routed proxy providers, cron profile ownership, Lucid, Buzz mention policy, TUI plugin cards, Discord bot admission, and deploy-branch update reconciliation.
- `fork-carries.json` validates with 16 replay-ready carries and zero declaration-only active carries.
- Candidate verification: manifest/replay tooling 111 passed; 16/16 clean carry probes; focused Python integration 764 passed / 10 skipped plus updater 170 passed / 1 skipped; Desktop typecheck and package build passed; focused Projects 44/44; PTY 2/2; full Desktop 6,980 passed with all 45 broad failures classified against upstream/order-isolation controls.
- Candidate publication target is `origin/axiom-next`; `origin/axiom` remains frozen pending operator live smoke and promotion approval.
