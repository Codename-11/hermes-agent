"""UX helpers for `hermes update` — pipeline status line + upstream brief.

Two things live here so `main.cmd_update` can stay focused on git/pip logic:

* ``Pipeline``      — a single-line status display that shows phases separated
                      by ``|``, with a spinner on the active phase and a check
                      on completed phases.  Falls back to plain prints when
                      stdout isn't a TTY (e.g. gateway mode, log piping).
* ``write_update_brief`` — after a successful update, walks
                      ``git log OLD..NEW`` and emits a markdown brief the
                      agent can read.  Saved to
                      ``~/.hermes/logs/update-briefs/<timestamp>.md`` and
                      mirrored to ``last-update-brief.md``.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, Optional


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_FRAME_INTERVAL = 0.08  # seconds


def _stdout_is_tty() -> bool:
    """Return True when the real terminal underneath stdout is a TTY.

    ``_UpdateOutputStream`` wraps stdout during ``hermes update`` to mirror
    writes to a log; ask the wrapped original instead of the wrapper.
    """
    stream = getattr(sys.stdout, "_original", sys.stdout)
    try:
        return bool(stream.isatty())
    except Exception:
        return False


class Pipeline:
    """Single-line pipeline status — ``⠋ fetch | ✓ sync | · merge``.

    Lifecycle:
        pipe = Pipeline(["fetch", "sync", "merge"])
        pipe.start("fetch")
        ... do work ...
        pipe.advance("sync")
        ... do work ...
        pipe.finish()       # mark remaining phases done
        # or
        pipe.fail("sync")   # mark current phase failed; subsequent calls no-op

    On a non-TTY the class degrades to plain line prints so logs stay
    readable.  Animation uses a daemon thread; ``finish()`` / ``fail()`` /
    context-manager exit stops it cleanly.
    """

    PENDING = "·"
    ACTIVE = "spin"   # rendered as current spinner frame
    DONE = "✓"
    FAIL = "✗"

    def __init__(self, phases: Iterable[str], *, label: str = ""):
        self._phases = [str(p) for p in phases]
        self._label = label
        self._status: dict[str, str] = {p: self.PENDING for p in self._phases}
        self._active: Optional[str] = None
        self._frame = 0
        self._tty = _stdout_is_tty()
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._last_line_len = 0

    # --- public API --------------------------------------------------------

    def start(self, phase: str) -> None:
        """Begin showing the pipeline, with *phase* as the active one."""
        if phase not in self._status:
            return
        self._active = phase
        self._status[phase] = self.ACTIVE
        if self._tty:
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()
        else:
            self._print_plain(f"→ {phase}")

    def advance(self, next_phase: str) -> None:
        """Mark the active phase done and activate *next_phase*."""
        with self._lock:
            if self._active and self._status.get(self._active) == self.ACTIVE:
                self._status[self._active] = self.DONE
            if next_phase in self._status:
                self._status[next_phase] = self.ACTIVE
                self._active = next_phase
        if not self._tty:
            self._print_plain(f"  ✓ {self._prev_phase_name()}")
            self._print_plain(f"→ {next_phase}")

    def fail(self, phase: Optional[str] = None, *, note: str = "") -> None:
        """Mark *phase* (defaults to active) failed and stop animation."""
        target = phase or self._active
        with self._lock:
            if target and target in self._status:
                self._status[target] = self.FAIL
        self._stop(final_glyph=self.FAIL, final_note=note)

    def finish(self, *, note: str = "") -> None:
        """Mark all remaining phases done, stop animation, leave a final line."""
        with self._lock:
            if self._active and self._status.get(self._active) == self.ACTIVE:
                self._status[self._active] = self.DONE
            for p in self._phases:
                if self._status[p] == self.PENDING:
                    self._status[p] = self.DONE
        self._stop(final_glyph=self.DONE, final_note=note)

    def __enter__(self) -> "Pipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.fail(note=str(exc) if exc else "")
        else:
            if self._active and self._status.get(self._active) == self.ACTIVE:
                self.finish()
            else:
                self._stop(final_glyph=self.DONE)

    # --- internals ---------------------------------------------------------

    def _prev_phase_name(self) -> str:
        last = ""
        for p in self._phases:
            if self._status[p] == self.DONE:
                last = p
        return last

    def _render(self) -> str:
        parts = []
        for p in self._phases:
            s = self._status[p]
            if s == self.ACTIVE:
                glyph = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
            elif s == self.DONE:
                glyph = self.DONE
            elif s == self.FAIL:
                glyph = self.FAIL
            else:
                glyph = self.PENDING
            parts.append(f"{glyph} {p}")
        line = " | ".join(parts)
        return f"{self._label} {line}" if self._label else line

    def _animate(self) -> None:
        while not self._stopped.is_set():
            with self._lock:
                line = self._render()
            self._write_line(line)
            self._frame += 1
            if self._stopped.wait(_FRAME_INTERVAL):
                break

    def _write_line(self, line: str) -> None:
        pad = max(0, self._last_line_len - len(line))
        sys.stdout.write("\r" + line + (" " * pad))
        sys.stdout.flush()
        self._last_line_len = len(line)

    def _stop(self, *, final_glyph: str = "", final_note: str = "") -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=_FRAME_INTERVAL * 4)
        if self._tty:
            with self._lock:
                line = self._render()
            self._write_line(line)
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._last_line_len = 0
        else:
            # Emit a plain final line so the log captures completion.
            summary = " | ".join(
                f"{self._status[p]} {p}".replace(self.ACTIVE, "·")
                for p in self._phases
            )
            self._print_plain(f"  {summary}")
        if final_note:
            self._print_plain(f"  {final_note}")

    @staticmethod
    def _print_plain(text: str) -> None:
        print(text, flush=True)


# ---------------------------------------------------------------------------
# Upstream-brief generator
# ---------------------------------------------------------------------------

_CATEGORY_ORDER = [
    ("feat",     "Features"),
    ("fix",      "Fixes"),
    ("perf",     "Performance"),
    ("refactor", "Refactors"),
    ("docs",     "Docs"),
    ("test",     "Tests"),
    ("chore",    "Chores"),
    ("build",    "Build"),
    ("ci",       "CI"),
    ("style",    "Style"),
]
_PREFIX_MAP = {k: v for k, v in _CATEGORY_ORDER}


def _categorize(subject: str) -> str:
    """Return a category key for a conventional-commit-style subject."""
    head = subject.split(":", 1)[0].strip().lower()
    # Strip scope like "feat(skills)" → "feat"
    head = head.split("(", 1)[0].strip()
    return head if head in _PREFIX_MAP else "other"


def _briefs_dir() -> Path:
    from hermes_cli.config import get_hermes_home  # type: ignore

    d = get_hermes_home() / "logs" / "update-briefs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_update_brief(
    repo: Path,
    old_sha: str,
    new_sha: str,
    *,
    git_cmd: Optional[list[str]] = None,
    branch: str = "",
) -> Optional[Path]:
    """Write a markdown brief of commits between *old_sha* and *new_sha*.

    Returns the path to the written brief, or ``None`` if nothing to write.
    Also mirrors the latest brief to ``logs/last-update-brief.md`` so the
    agent can always find ``~/.hermes/logs/last-update-brief.md`` without
    guessing a timestamp.
    """
    if not old_sha or not new_sha or old_sha == new_sha:
        return None

    git = list(git_cmd) if git_cmd else ["git"]

    def _run(args: list[str]) -> str:
        try:
            r = subprocess.run(
                git + args, cwd=repo, capture_output=True, text=True, check=True,
            )
            return r.stdout
        except Exception:
            return ""

    log = _run([
        "log", "--no-merges", "--pretty=format:%H\t%s\t%an",
        f"{old_sha}..{new_sha}",
    ])
    if not log.strip():
        return None

    commits = []
    for line in log.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        sha, subject = parts[0], parts[1]
        author = parts[2] if len(parts) > 2 else ""
        commits.append((sha, subject, author, _categorize(subject)))

    stat = _run(["diff", "--shortstat", f"{old_sha}..{new_sha}"]).strip()
    files_changed = _run([
        "diff", "--name-only", f"{old_sha}..{new_sha}",
    ]).splitlines()

    buckets: dict[str, list[tuple[str, str, str]]] = {}
    for sha, subject, author, cat in commits:
        buckets.setdefault(cat, []).append((sha, subject, author))

    now = _dt.datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")

    lines: list[str] = []
    lines.append(f"# hermes update brief — {now.isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"- **Repo:** `{repo}`")
    if branch:
        lines.append(f"- **Branch:** `{branch}`")
    lines.append(f"- **Range:** `{old_sha[:10]}..{new_sha[:10]}`")
    lines.append(f"- **Commits:** {len(commits)}")
    if stat:
        lines.append(f"- **Diff:** {stat}")
    lines.append("")
    lines.append("## Summary")
    summary_parts = []
    for key, heading in _CATEGORY_ORDER:
        if key in buckets:
            summary_parts.append(f"{len(buckets[key])} {heading.lower()}")
    if "other" in buckets:
        summary_parts.append(f"{len(buckets['other'])} other")
    lines.append(", ".join(summary_parts) if summary_parts else "no categorized commits")
    lines.append("")

    for key, heading in _CATEGORY_ORDER + [("other", "Other")]:
        items = buckets.get(key)
        if not items:
            continue
        lines.append(f"## {heading} ({len(items)})")
        lines.append("")
        for sha, subject, author in items:
            lines.append(f"- `{sha[:10]}` {subject}")
        lines.append("")

    if files_changed:
        lines.append(f"## Files changed ({len(files_changed)})")
        lines.append("")
        for f in files_changed[:200]:
            lines.append(f"- `{f}`")
        if len(files_changed) > 200:
            lines.append(f"- …and {len(files_changed) - 200} more")
        lines.append("")

    body = "\n".join(lines) + "\n"

    briefs = _briefs_dir()
    path = briefs / f"brief-{stamp}.md"
    try:
        path.write_text(body, encoding="utf-8")
    except OSError:
        return None

    latest = briefs.parent / "last-update-brief.md"
    try:
        latest.write_text(body, encoding="utf-8")
    except OSError:
        pass

    return path
