# Axiom fork reconciliation standard

Use this for every `upstream/main` refresh of the Axiom Hermes fork. It replaces ad-hoc deploy-branch merges as the normal integration path.

## Core model

```text
upstream/main@U
  + ordered carries from fork-carries.json
  = candidate@C

candidate@C --explicit promotion--> origin/axiom@C
origin/axiom@C --maintenance window--> live checkouts/services
```

Reconciliation, promotion, and deployment are three separate operations. Completion of one never authorizes the next.

## Non-deploying audit boundary

The audit lane may:

- fetch refs;
- create disposable or named isolated worktrees;
- replay carries;
- install candidate-local dependencies;
- run tests, builds, parity checks, and independent review;
- push a non-deploy candidate branch such as `origin/axiom-next`.

The audit lane must not:

- move or force-update `origin/axiom`;
- edit a managed/live source checkout;
- install over a running Desktop package;
- restart dashboards, gateways, schedulers, or Desktop;
- clear update markers or mutate host-local Hermes state.

If the operator asks only for an audit or reconciliation, stop after publishing/verifying the candidate report.

## Required inputs

Record immutable values before work begins:

- `U`: fetched `upstream/main` SHA;
- `D`: fetched `origin/axiom` SHA;
- carry manifest hash and schema version;
- candidate branch/worktree path;
- exact dependency lockfiles used;
- rollback/archive ref, if promotion is later approved.

Any movement of `U` or `D` makes the promotion plan stale. Re-fetch and regenerate; do not reuse old leases or reports.

## Candidate generation

1. Create an isolated worktree at exact `U`. Never build from the live checkout.
2. Validate `fork-carries.json` and require every active carry to be replay-ready.
3. Apply carries in manifest order using immutable commit IDs.
4. Stop at the first named conflict. Resolve only within that carry's declared intent and protected paths.
5. Never merge historical deploy ancestry into the candidate.
6. Install dependencies inside the candidate worktree from its own lockfiles.

## Mandatory completeness gates

All gates are required. A full test suite is evidence, not a substitute for these invariants.

### 1. Carry ownership

Every path changed between `upstream/main@U` and candidate `C` must be declared by an active carry as a path, test, contract, or generated support file. Unexplained executable paths fail the candidate.

### 2. Upstream survival

For every path not owned by an active carry, candidate content must equal `upstream/main@U` exactly. This catches clean semantic losses where a merge preserves an old fork file while upstream changes an adjacent caller.

Any non-carry delta is a failure even when Git reports no conflict and tests compile.

### 3. Caller/callee contract audit

For every changed signature, credential handoff, route qualifier, prepare/activate seam, or state-publication sequence:

- enumerate real callers and implementations;
- add a caller-to-real-implementation test;
- include sibling reconnect/resume paths;
- perform a sabotage run proving the regression test fails when the stale half is restored.

Direct callee tests and mocked-boundary tests are insufficient.

### 4. Exact upstream control

When candidate tests fail broadly, run the same command against an untouched upstream worktree with the same dependency and environment shape. Classify baseline failures explicitly; never wave away an unclassified failure as upstream drift.

### 5. Domain acceptance matrix

Record separate pass/fail rows instead of one global “Desktop works” verdict:

- local gateway boot and session listing;
- authenticated remote OAuth ticket mint + `/api/ws` upgrade;
- remote session list/open/resume;
- default, named, and All Profiles routing;
- connection switch local → remote → local;
- Bot Mode owner routing;
- project overview source isolation;
- browser-control authenticated identity/context;
- dashboard/plugin administration auth;
- updater/status behavior.

A passing row does not retire or validate another row.

## Minimum regression gates for gateway/Desktop refreshes

When any of `hermes_cli/web_server.py`, `tui_gateway/ws.py`, `tui_gateway/server.py`, dashboard authentication, Desktop connection code, or profile/session routing changes, run at minimum:

```bash
uv run --extra dev pytest -q -o addopts= \
  tests/test_tui_gateway_ws.py \
  tests/gateway/test_browser_control_cloud.py \
  tests/hermes_cli/test_dashboard_auth_ws_auth.py

cd apps/desktop
NODE_ENV=test npm run test:ui -- --run \
  src/store/session.test.ts \
  src/store/session-states.test.ts \
  src/sdk/profile-routing.test.ts
npm run typecheck
```

Add a route-level test that mints/consumes a ticket-bearing `Sec-WebSocket-Protocol`, invokes `gateway_ws` through the real handler boundary, and proves only the stable public protocol is accepted—never the credential-bearing protocol.

## Candidate report and stop point

The candidate report must include:

- `U`, `D`, and `C` SHAs;
- carry count/order and replay result;
- manifest/path ownership result;
- upstream-survival result;
- exact commands and pass/fail/baseline classification;
- independent review verdict;
- artifact hashes/stamps if Desktop was packaged;
- unresolved risks and live acceptance rows still requiring operator interaction.

Push only the candidate branch, read it back, and stop. Candidate publication does not move `origin/axiom` and does not affect running deployments.

## Promotion and deployment

Promotion requires a separate explicit operator decision after reviewing the candidate report.

1. Re-fetch and verify `origin/axiom == D`; otherwise regenerate or re-review.
2. Create/read back an immutable rollback ref for `D`.
3. Promote exact `C` with an expected-SHA lease.
4. Read back candidate, deploy, and rollback refs.
5. Do not mutate live checkouts from a running Hermes process.
6. During a separate maintenance window, check active turns/sessions, stop only affected services, fast-forward live checkouts to exact `C`, refresh dependencies/artifacts, restart, and verify the live acceptance matrix.

Source SHA, installed package stamp/hash, running executable, backend process, and live workflow are separate facts; report each independently.

## Failure learned on 2026-08-23

The 2026-08-22 regeneration candidate had complete declared carry ownership and extensive tests. A later upstream refresh still produced a split contract: `hermes_cli.web_server.gateway_ws` passed `auth_identity` and `subprotocol`, while the retained `tui_gateway.ws.handle_ws` accepted only `ws`. Local and remote Desktop WebSockets crashed although HTTP health remained green.

The missing control was upstream-survival parity plus a route-level caller/implementation test after the final refresh. This standard makes both mandatory.
