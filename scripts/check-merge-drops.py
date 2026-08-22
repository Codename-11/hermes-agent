#!/usr/bin/env python3
"""Detect upstream content silently dropped during a merge conflict resolution.

Axiom is a long-lived fork of NousResearch/hermes-agent. When `hermes update`
merges upstream into `axiom`, conflicts are resolved by hand (or by an agent).
The dangerous failure mode is not a leftover ``<<<<<<<`` marker — those are
obvious — but a *silent drop*: a conflict resolved by keeping "ours" in a way
that discards upstream's edits, while the feature's support code is left
behind so nothing breaks until much later. We hit exactly this with the Azure
Foundry Entra ID auth wiring and the xAI OAuth arg-forwarding (restored in
merge 02da2fb35).

This script audits a merge for two drop signatures, scoped to the files that
*could* have been hand-resolved (changed on both sides of the merge):

  A. upstream-added lines (base -> theirs) that are absent from the result.
     => upstream's NEW work was dropped during resolution.
  B. base lines that ours deleted but theirs kept, and that are absent from
     the result.  => axiom removed content upstream still ships (may be an
     intentional fork divergence, or an accidental drop — review).

Both are advisory. Signal B in particular has false positives on a fork that
intentionally removes upstream features, so the output is a "review these"
report, not gospel. Trivial lines (blank / pure punctuation / very short) are
filtered to cut noise.

Modes (auto-detected):
  * merge-in-progress: MERGE_HEAD exists. ours=HEAD, theirs=MERGE_HEAD,
    result=working tree.
  * post-merge:        HEAD is a merge commit. ours=HEAD^1, theirs=HEAD^2,
    result=HEAD tree.

Usage:
    scripts/check-merge-drops.py                # audit current merge / HEAD
    scripts/check-merge-drops.py --warn-only    # never exit nonzero
    scripts/check-merge-drops.py --merge <sha>  # audit a specific merge commit
    scripts/check-merge-drops.py --self-test    # validate detector logic

Exit status: 0 if no drops (or --warn-only); 1 if candidate drops found;
2 on usage / git errors.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter

# Lines this short (after strip) are too common to match by content reliably.
_MIN_LEN = 4
# Pure-syntax / boilerplate lines that recur everywhere — never a drop signal.
_STOPLIST = {
    "try:", "else:", "pass", "return", "continue", "break", "raise",
    "\"\"\"", "'''", "import os", "import sys", "import json", "import re",
}


def _run(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def _show(rev: str, path: str) -> list[str]:
    """File content at <rev>:<path> as lines, or [] if it does not exist."""
    try:
        out = subprocess.run(
            ["git", "show", f"{rev}:{path}"],
            capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return []
    return out.splitlines()


def _worktree(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except FileNotFoundError:
        return []


def _significant(line: str) -> bool:
    s = line.strip()
    if len(s) < _MIN_LEN:
        return False
    if s in _STOPLIST:
        return False
    # all-punctuation lines (closing brackets, separators) carry no signal
    if not any(ch.isalnum() for ch in s):
        return False
    return True


def _counter(lines: list[str]) -> Counter:
    return Counter(ln.rstrip() for ln in lines if _significant(ln))


def _added(frm: list[str], to: list[str]) -> Counter:
    """Significant lines present in `to` beyond what `frm` has (multiset)."""
    c = _counter(to) - _counter(frm)
    return +c  # drop non-positive counts


def detect_file(base: list[str], ours: list[str], theirs: list[str],
                result: list[str]) -> dict[str, list[str]]:
    """Return {signal: [sample dropped lines]} for one file."""
    res = _counter(result)

    # A: upstream's new lines (base->theirs) missing from result.
    dropped_a = _added(base, theirs) - res

    # B: ours deleted (base->ours) AND theirs still has it AND not in result.
    ours_removed = _added(ours, base)          # in base, not in ours
    theirs_has = _counter(theirs)
    kept_by_upstream = Counter()
    for line, n in ours_removed.items():
        if theirs_has.get(line):
            kept_by_upstream[line] = n
    dropped_b = kept_by_upstream - res

    out: dict[str, list[str]] = {}
    if +dropped_a:
        out["upstream-new-dropped"] = sorted(set((+dropped_a).elements()))
    if +dropped_b:
        out["axiom-removed-upstream-kept"] = sorted(set((+dropped_b).elements()))
    return out


def _resolve_revs(merge: str | None):
    """Return (mode, base, ours, theirs, result_kind, result_rev_or_None)."""
    if merge:
        parents = _run(["rev-list", "--parents", "-n", "1", merge]).split()
        if len(parents) < 3:
            sys.exit(f"error: {merge} is not a merge commit (needs 2 parents)")
        ours, theirs = parents[1], parents[2]
        base = _run(["merge-base", ours, theirs]).strip()
        return ("post-merge", base, ours, theirs, "tree", merge)

    # merge in progress?
    try:
        merge_head = _run(["rev-parse", "--verify", "MERGE_HEAD"]).strip()
        ours = _run(["rev-parse", "HEAD"]).strip()
        base = _run(["merge-base", ours, merge_head]).strip()
        return ("in-progress", base, ours, merge_head, "worktree", None)
    except subprocess.CalledProcessError:
        pass

    # HEAD is a merge commit?
    parents = _run(["rev-list", "--parents", "-n", "1", "HEAD"]).split()
    if len(parents) >= 3:
        ours, theirs = parents[1], parents[2]
        base = _run(["merge-base", ours, theirs]).strip()
        return ("post-merge", base, ours, theirs, "tree", "HEAD")

    sys.exit("error: no merge in progress and HEAD is not a merge commit "
             "(use --merge <sha>)")


def _candidate_files(base: str, ours: str, theirs: str) -> list[str]:
    """Files changed on BOTH sides — where hand-resolution could drop content."""
    ours_changed = set(_run(["diff", "--name-only", base, ours]).splitlines())
    theirs_changed = set(_run(["diff", "--name-only", base, theirs]).splitlines())
    return sorted(ours_changed & theirs_changed)


def run_audit(merge: str | None, warn_only: bool) -> int:
    mode, base, ours, theirs, result_kind, result_rev = _resolve_revs(merge)
    files = _candidate_files(base, ours, theirs)

    print(f"merge-drop audit ({mode})")
    print(f"  base   {base[:12]}")
    print(f"  ours   {ours[:12]}")
    print(f"  theirs {theirs[:12]}")
    print(f"  scanning {len(files)} file(s) changed on both sides\n")

    findings: dict[str, dict[str, list[str]]] = {}
    for path in files:
        b = _show(base, path)
        o = _show(ours, path)
        t = _show(theirs, path)
        r = _worktree(path) if result_kind == "worktree" else _show(result_rev, path)
        hits = detect_file(b, o, t, r)
        if hits:
            findings[path] = hits

    if not findings:
        print("✓ no candidate upstream-content drops detected")
        return 0

    for path, hits in findings.items():
        print(f"⚠ {path}")
        for signal, lines in hits.items():
            label = {
                "upstream-new-dropped":
                    "upstream added these lines; they are missing from the result",
                "axiom-removed-upstream-kept":
                    "axiom removed these; upstream still ships them, missing from result",
            }[signal]
            print(f"    [{signal}] {label}:")
            for ln in lines[:8]:
                print(f"        - {ln.strip()[:100]}")
            if len(lines) > 8:
                print(f"        … and {len(lines) - 8} more")
        print()

    print(f"{len(findings)} file(s) with candidate drops. Review each: a drop "
          "may be an intended fork divergence, or an unintended loss to restore.")
    return 0 if warn_only else 1


def _self_test() -> int:
    base = [
        "def flow(cfg, *, args=None):",
        "    if use_entra:",
        "        token = mint_token(args)",
        "        return token",
        "    key = getpass.getpass('key: ')",
        "    return key",
    ]
    # ours: dropped the entra branch AND the args param (the real-world bug)
    ours = [
        "def flow(cfg):",
        "    key = getpass.getpass('key: ')",
        "    return key",
    ]
    # theirs: upstream kept entra, improved the prompt (new line)
    theirs = [
        "def flow(cfg, *, args=None):",
        "    if use_entra:",
        "        token = mint_token(args)",
        "        return token",
        "    key = masked_secret_prompt('key: ')",
        "    return key",
    ]
    # result of a BAD resolution: took ours, dropped entra + the new prompt
    result = ours
    hits = detect_file(base, ours, theirs, result)
    print("self-test findings:", hits)
    ok = (
        "axiom-removed-upstream-kept" in hits
        and any("use_entra" in l for l in hits["axiom-removed-upstream-kept"])
        and "upstream-new-dropped" in hits
        and any("masked_secret_prompt" in l for l in hits["upstream-new-dropped"])
    )
    # negative control: a clean resolution (== theirs) flags nothing.
    clean = detect_file(base, ours, theirs, theirs)
    print("self-test clean-resolution findings:", clean)
    ok = ok and not clean
    print("SELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merge", metavar="SHA", help="audit a specific merge commit")
    ap.add_argument("--warn-only", action="store_true",
                    help="report but always exit 0")
    ap.add_argument("--self-test", action="store_true",
                    help="validate detector logic on synthetic data")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    try:
        return run_audit(a.merge, a.warn_only)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"git error: {exc.stderr or exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
