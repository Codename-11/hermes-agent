# Axiom Regeneration Audit — 2026-08-22

## Frozen rollback state

- `origin/axiom`: `d80816d200974e20702364ddd4426e97c6a2399e`
- `origin/archive/axiom-pre-regeneration-20260822`: `d80816d200974e20702364ddd4426e97c6a2399e`
- Audit upstream: `987064caa4f8845f605ac7346fed5b72fddfb21c`
- Installed Desktop source stamp: `aedebecbdb8a9c4d8e5a96b12115ee97d9d3e96d`

Installed package SHA-256:

- `Hermes.exe`: `8e3578c2dceff753292880e550d3bc39a7563d69010e471018ab8da28ee16b71`
- `resources/app.asar`: `0fad780e2bc316d5db314c703467dbc5b9db2b437ef2d5eb2915f95092ff7632`
- `resources/install-stamp.json`: `38ead081164b578be18a1a14bccc1069a051075bd556d091ea0d1b3749083d95`

## Initial findings

- `origin/axiom` is 17 commits ahead and 697 commits behind current `upstream/main`.
- `fork-carries.json` declares 15 active carries and validates structurally.
- Since the merge base, 498 paths differ; 75 are covered by active carry `paths`, `tests`, or contract/support files, leaving 423 uncovered paths (242 Desktop, 181 non-Desktop).
- Upstream movement overlaps three active carries: webhook route toolsets (`gateway/run.py`), Buzz mention policy (`hermes_cli/config_defaults.py`), and dashboard plugin admin auth (`hermes_cli/web_server.py`).

## Carry cut decisions

### Fresh active carries

- `desktop-gateway-projects`: selected-gateway project aggregate plus narrow Projects / Recent Sessions renderer. Extracted commit: `fece0a0e845a4ff061f97451181e505d80576e8c`.
- `desktop-registered-source-routing`: conditional fresh extraction only if upstream Bot/Profile parity fails; do not copy the current broad SDK/profile files.
- `webhook-route-toolsets`: include shared `MessageEvent.enabled_toolsets` and wrapper/inner runner plumbing while preserving upstream watchdog behavior.
- `forge-integration`: explicit Forge plugin package, generic platform registration, draft/reply correlation, and runtime tool policy.
- `proxy-provider-routing`: include routed adapter plus OpenAI Codex/xAI adapters, registry, CLI/server, and focused tests.
- `shared-cron-profile-ownership`: include scheduler/store plus CLI/tool call paths.
- `project-source-policy`: backend interactive-source taxonomy and active-session retention used by the gateway-wide overview.
- `dashboard-profile-pty-attachments`, `mcp-oauth-stream-concurrency`, `dashboard-plugin-admin-auth`, `windows-portability`, and deploy-branch update reconciliation are extracted separately against current upstream.
- Carry manifest/replay tooling is a non-runtime support carry. Extracted commit: `bdc36447610a96f8d2d18adbf2f307c348d48699` (final manifest test waits for regenerated manifest).

### Retire to upstream

- Broad legacy Desktop profile/session workspace convergence and activation stacks.
- Desktop staged updater UI/core, native OAuth/media opening stack, HUD/theme/window stack, voice/terminal residuals, and project lifecycle/default/live-status extras unless a parity test against the candidate proves a current gap.
- Historical runtime/tool-call/transcript/translucency snapshots; current upstream owns these fixes.
- Old cross-registered-gateway project fan-out.

### Reconcile, never overwrite

- Webhook runner with upstream loop-watchdog changes.
- Buzz defaults with upstream gateway configuration defaults.
- Dashboard plugin auth with upstream SQLite corruption hardening.

## Manifest-declared baseline probe

A clean worktree at upstream `987064caa4f8845f605ac7346fed5b72fddfb21c` accepted the exact fork diff for the union of 85 declared active carry paths/tests. The result changed 71 files and passed `git diff --check`, but integration gates exposed undeclared dependencies:

- Desktop typecheck failed on missing profile visibility (`$hiddenProfiles`, `filterVisibleProfiles`), multi-profile session identity (`sessionStatusKey`), browser/nav contribution hooks (`openBrowser`, `SidebarNavItem.onSelect`), hybrid project renderer/i18n support, and typed project-directory support (`ensureDesktopDirectory`).
- Focused Desktop suite: 52 passed / 15 failed; failures map to missing browse/profile scope, active-profile duplicate identity, and project-preview row dependencies.
- The live Hermes venv does not include pytest, so Python carry checks require a dedicated dev/test environment rather than the runtime venv.
- Mechanical historical replay is not viable: eight all-commit carries all conflicted on their first provenance commit. `fork_carry_replay.py plan` reports 15 active carries and 0 replay-ready carries.

- The isolated Python carry suite collected 531 tests: 475 passed, 39 failed, 17 skipped. Failures expose hidden dependencies in Forge platform registration, webhook `MessageEvent.enabled_toolsets`/runner plumbing, deploy updater command/watch/quarantine support, plugin capability/TUI command dispatch, and routed proxy auth/adapter behavior.

Conclusion: `fork-carries.json` is currently a declaration inventory, not a replay stack. Hidden Desktop and backend behaviors must be split into bounded carries before replay.

## Final candidate

- Candidate branch: `candidate/axiom-next-20260822`.
- Pinned upstream base: `987064caa4f8845f605ac7346fed5b72fddfb21c`.
- Runtime carries: 17 active, 17 replay-ready, zero incomplete.
- Carry replay probes: 17/17 apply cleanly from the pinned base with zero conflicts.
- Candidate path ownership: 107/107 changed paths declared before final contract/report support files; zero unexplained runtime paths.
- Focused Python carry integration: 764 passed, 10 skipped before updater integration; updater checks add 166 passed, 1 skipped plus 4 isolated preview tests; subsequent carry suites (cron, webhook/auth, proxy, Lucid/Buzz, TUI, Discord) all passed.
- Desktop: full typecheck passed; focused Projects suite 44/44; web PTY 2/2; full run 6,980 passed, 45 failed, 5 skipped. Twenty-six Electron failures reproduce on exact upstream under Windows. The remaining 19 UI failures pass in isolation on both upstream and candidate and are full-suite order/concurrency pollution.
- Exact upstream broad-failure control was run with the same dependency tree and environment.
- No historical `origin/axiom` merge ancestry appears in the candidate range.
- Final candidate refresh includes upstream through `530028c213ae9eed5d7f1a826451e0edf24a11d2`, registered-source routing (`30/30` Node, `71/71` Vitest, full Desktop typecheck), Forge URL hardening (`43/43`), and the upstream-refresh gateway/updater isolation gates (`62 passed / 16 skipped`, `131 passed / 1 skipped`, version preview `4/4`).
- Axiom Enhancements `0.5.0` is restored from the private Agent Library source; contract smoke and all `37/37` plugin tests pass.

## Promotion boundary

Publish only to `origin/axiom-next`. Do not rewrite `origin/axiom` until operator review and live candidate Desktop local/remote/profile/project/plugin smoke complete.

## Safety boundary

This audit must publish a candidate to `origin/axiom-next` first. It must not rewrite `origin/axiom` without operator review of the verified candidate, rollback ref, and exact parity report.
