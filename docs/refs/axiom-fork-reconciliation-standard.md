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

Reconciliation, promotion, and deployment are three separate authorization states. Candidate completion never moves the deploy ref by itself. A later explicit `hermes update` invocation may approve promotion and deployment together only after revalidating the exact ready report, refs, and rollback lease described below.

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
3. Validate every distinct `replay.source_ref` as Git data, rejecting leading-dash remote names and malformed branch paths. Fetch each source into a run-scoped private ref, register that ref for cleanup before fetch/read-back, and require every declared replay commit to be an ancestor of its fetched private source ref before applying carries in manifest order. Delete all private refs on success or failure.
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

The candidate review dossier has two stages. The immutable worker report is produced before independent review and must include:

- `U` and `C` SHAs;
- ordered applied carry IDs/source commits and replay result;
- manifest/input/replay hashes and path-ownership result;
- upstream-survival result;
- exact declared checks and pass/fail classification;
- unresolved worker errors and artifact hashes/stamps when packaging occurred.

After generation, the operator review record adds the observed pre-promotion deploy SHA `D`, independent-review verdict, reviewer suggestions/risks, and live acceptance rows still requiring operator interaction. The worker must never fabricate fields that can only exist after review. The complete dossier—not the worker JSON alone—is the approval evidence used by the operator before a later promotion invocation.

Push only the candidate branch, read it back, and stop. Candidate publication does not move `origin/axiom` and does not affect running deployments.

## Bare `hermes update` lifecycle

On configured generated deploy branches (`axiom` and `tgi`), ordinary `hermes update` is a consume-or-queue command, not a live merge engine:

1. Fetch `origin/<deploy>` and `upstream/main`.
2. If a reviewed deploy artifact is already ahead, consume it through the normal update lifecycle.
3. If upstream is pending and no matching ready report exists, write queue state under `$HERMES_HOME/update-reconciliation/<branch>.json`, launch `python -m hermes_cli.axiom_reconcile` detached, and return before dependency installation or service restart when no deploy artifact was consumed.
4. The worker validates the manifest, fetches each carry from its stable dedicated `origin/carry/source-*` ref into private refs, proves every declared commit descends from that source, creates a temporary worktree at exact `U`, replays immutable carry commits, enforces carry ownership and upstream survival, runs deduplicated declared checks, and re-fetches upstream. Active replay sources may not use mutable candidate refs ending in `-next`. If upstream advanced while checks ran, the worker records `observed_upstream_sha` plus `upstream_pending=true` but may still publish the exact pinned candidate: upstream motion is new queue work, not proof that the pinned artifact is invalid. Before querying or publishing `origin/<branch>-next`, it holds the canonical queue/publication lock and proves canonical `run_id` plus `input_digest` still identify that worker; a newer canonical run still invalidates the worker and stale workers cannot publish candidates. After candidate publication it re-fetches every stable source, re-proves ancestry, verifies private-ref cleanup, and only then records `source_availability_verified=true` and marks the report ready.
5. The worker writes an exact-SHA JSON report inside `$HERMES_HOME/update-reconciliation/runs/<run_id>/`. Canonical state is updated only when its run identity still matches. Queue, worker publication, and promotion share a persistent kernel-held file lock; ownership is released by closing the descriptor, never by unlinking a pathname. Process death releases the kernel lock automatically, and only a definitive `WAIT_OBJECT_0` is treated as dead on Windows.
6. A later explicit `hermes update` holds that canonical lock for the entire promotion transaction. It keeps the state root lexical, derives the only valid run directory from that root and the validated digest-prefix `run_id`, and rejects symlink/reparse components at the root, `runs` directory, run directory, and every evidence file. Evidence is opened with no-follow where available and descriptor/path identity is checked before and after reading. The updater requires exact bindings for run `state.json`, `report.json`, and `report.run_id`; recomputes the input digest from immutable worker, manifest, and validator bytes; verifies all hashes and the completed report hash before remote queries; requires clean ownership, upstream survival, successful checks, `source_availability_verified=true`, and exact candidate-ref read-back; then archives/read-backs old `origin/<deploy>`, lease-promotes exact `C`, realigns the stashed live checkout, and publishes terminal state before releasing the lock.

Repeated invocations while the same worker PID is active are idempotent. If reviewed `origin/<deploy>` is already ahead, the updater consumes that artifact before considering ready-report promotion or queueing newer upstream work. Push return codes are treated as transport evidence rather than final remote truth: archive, promotion, and rollback succeed only when exact remote read-back matches the intended SHA. Upstream movement after pinned generation is recorded for the next queue cycle; a mismatched snapshot or report, stale canonical run identity, failed check, moved deploy ref, failed rollback archive/read-back, or failed lease stops promotion.

### Local filesystem trust boundary

`$HERMES_HOME/update-reconciliation` must be private to the operator account. The updater rejects static symlink/reparse redirection, identity-checks evidence reads, and serializes all cooperating queue/worker/promotion processes with a kernel lock. It does not claim to defend against a malicious process already running as the same operator that actively renames trusted directory components during promotion; that identity can also replace the updater executable, manifest, credentials, or Git configuration. Such a same-identity compromise is outside this updater's threat model. Do not place `HERMES_HOME` in a directory writable by another user or untrusted service.

## Promotion and deployment

Promotion requires a separate explicit operator decision after reviewing the candidate report.

1. Re-fetch and verify `origin/axiom == D`; otherwise regenerate or re-review.
2. Create/read back an immutable rollback ref for `D`.
3. Promote exact `C` with an expected-SHA lease.
4. Read back candidate, deploy, and rollback refs.
5. Do not manually mutate live checkouts from an agent process. The normal updater may realign its own already-stashed checkout to exact generated `C` only after the gates above pass.
6. Continue the existing dependency refresh, selected service restart, and live fleet-version verification path; manual deployments still require the same maintenance checks.

Source SHA, installed package stamp/hash, running executable, backend process, and live workflow are separate facts; report each independently.

## Failure learned on 2026-08-23

The 2026-08-22 regeneration candidate had complete declared carry ownership and extensive tests. A later upstream refresh still produced a split contract: `hermes_cli.web_server.gateway_ws` passed `auth_identity` and `subprotocol`, while the retained `tui_gateway.ws.handle_ws` accepted only `ws`. Local and remote Desktop WebSockets crashed although HTTP health remained green.

The missing control was upstream-survival parity plus a route-level caller/implementation test after the final refresh. This standard makes both mandatory.
