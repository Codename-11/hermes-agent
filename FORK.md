# Axiom Hermes Fork Contract

This deploy fork is generated from an exact upstream commit plus an ordered, replayable carry stack. `main`/the replay base stays upstream-owned; fork behavior lives only in named carries below.

## Branch and replay contract

- Upstream base: `987064caa4f8845f605ac7346fed5b72fddfb21c`.
- Candidate refreshed through upstream `530028c213ae9eed5d7f1a826451e0edf24a11d2` without historical `origin/axiom` ancestry.
- Deploy branch: `origin/axiom`, promoted from the verified `origin/axiom-next` candidate at `2c337df3aab21eb9cdb45cdac99395e8fdaba9ae` on 2026-08-22.
- Existing deploy rollback: `origin/archive/axiom-pre-regeneration-20260822`.
- Never merge historical `origin/axiom` ancestry into a regenerated candidate.
- Every candidate-changed path must belong to one active carry path/test/contract.
- After every upstream refresh, every path not owned by an active carry must equal the pinned upstream tree exactly. Carry-path ownership alone does not prove upstream code survived.
- Reconciliation is non-deploying by default: build and verify an isolated candidate, publish only a candidate ref, then stop. Moving `origin/axiom` and updating live hosts require a separate explicit promotion/deployment decision.
- Canonical procedure: `docs/refs/axiom-fork-reconciliation-standard.md` and Obsidian `3. System/Operations/Runbooks/Hermes Axiom Sync Runbook.md`.

## Reconciliation and promotion boundary

The standard flow is:

```text
upstream/main@U + ordered carry stack = candidate@C
candidate@C --explicit promotion--> origin/axiom@C
origin/axiom@C --maintenance window--> live deployments
```

Never merge current upstream directly into the running deploy checkout. Never let an audit invocation move `origin/axiom`, install Desktop, or restart services. A final upstream refresh invalidates earlier path/test evidence and must rerun manifest ownership, upstream-survival parity, changed-signature caller audits, focused checks, and independent review against the refreshed candidate.

The 2026-08-23 WebSocket outage is the reference failure: a clean refresh retained an old `tui_gateway.ws.handle_ws` while upstream/fork code called it with authenticated identity and subprotocol parameters. Full carry ownership and broad green tests did not catch the split contract. Route-level caller-to-real-implementation tests and non-carry upstream parity are mandatory.

## Desktop gateway-scoped hybrid Projects

Project overview is aggregated across profiles hosted by the selected gateway only. It never fans into another registered gateway and never follows the focused chat profile. Actual project rows render under **Projects**; ordinary rows render separately under **Recent Sessions**. Empty/loading trees remain in project mode rather than falling through to mislabeled sessions.

## Desktop registered-source routing

Remote profile identity is the qualified pair `(connectionId, profile)`. Overlapping local/remote roster names cannot reclassify the selected remote primary as local, and opening a session from **Display all profiles** retains the session row's registered source instead of falling back to a same-named local profile.

## Fork replay tooling

Read-only manifest validation, replay planning/probes, status reporting, and the pre-push hook are fork infrastructure. The committed manifest must validate and every active carry must provide immutable replay metadata.

## Forge platform integration

Forge is a first-class platform plugin with streaming drafts, reply correlation, and run-scoped tool policy. Generic platform/tool behavior remains upstream-owned outside the bounded Forge package and policy seam.

## Project navigation source policy

Backend project navigation admits interactive session sources only and retains the active session required for deterministic project previews. The carry does not replace upstream's project builder.

## Dashboard profile-scoped PTY attachments

Dashboard PTY attachment tokens bind immutable profile identity so one profile cannot attach to another profile's live terminal.

## MCP OAuth refresh concurrency

MCP OAuth refresh and stream acquisition are serialized without breaking upstream discovery, first authorization, refresh, or 401 recovery.

## Windows portability fixes

Windows carries are limited to Git-Bash-safe approval temp cleanup and portable Kanban worker-exit decoding.

## Webhook route-level toolsets

Webhook routes may opt into an explicit toolset. The optional field is carried through `MessageEvent` and every wrapper/inner runner boundary. Upstream loop-watchdog behavior must remain intact.

## Dashboard plugin administration auth

Plugin administration routes require bearer authentication while preserving upstream fail-closed SQLite corruption handling and repair safeguards.

## Routed OAuth proxy providers

The local proxy routes model requests across Nous, OpenAI Codex OAuth, and xAI OAuth adapters with explicit registration, CLI/server translation, and isolated credential behavior.

## Shared cron profile ownership

Shared cron storage, scheduling, CLI, and tools qualify jobs by owner profile to prevent cross-profile reads, duplicate fire, and wrong-profile script resolution.

## Lucid memory integration

The Lucid/neural memory plugin is carried as a self-contained provider/client package. Retired MemPalace code and stale updater hooks are excluded; upstream context compression remains authoritative.

## Buzz active-thread mentions

Buzz thread activation and mention requirements are independently configurable, fail safely, survive restart, and preserve upstream gateway defaults.

## TUI plugin command cards

TUI plugin command dispatch retains gateway-only fallback, session-aware handler invocation, metadata, and structured InfoCard rendering. Image attachment is upstream-owned and not carried.

## Discord bot admission controls

Discord bot admission fails closed on invalid policy, propagates adapter-local settings, preserves safe allowed-mentions defaults, and prevents bot loops.

## Deploy branch update reconciliation

Deploy updates compare and publish the deploy branch, preserve local work transactionally, perform bounded conflict handoff/validation, and maintain Windows concurrent-instance safety. Desktop staged-update UI/core is retired and not part of this carry.

## Retirement rule

A carry retires only after current upstream implements the same observable invariant and its focused parity tests pass without the carry. Prefer upstream shape; never preserve a carry merely because its historical commit still exists.
