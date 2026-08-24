from __future__ import annotations

import os
import subprocess
from pathlib import Path

from hermes_cli import update_cmd


def _git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str, subject: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def test_reconciliation_receipt_shows_refs_and_upstream_only_digest(
    tmp_path, monkeypatch, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")

    old_upstream = _commit(repo, "base.txt", "base\n", "chore: baseline")

    _git(repo, "checkout", "-b", "old-deploy")
    previous = _commit(
        repo,
        "fork.txt",
        "old carry\n",
        "feat(fork): preserved carry from previous deployment",
    )

    _git(repo, "checkout", "main")
    _commit(repo, "feature.txt", "feature\n", "feat(cli): improve update output")
    upstream = _commit(repo, "fix.txt", "fix\n", "fix(update): clarify completion")
    _git(repo, "update-ref", "refs/remotes/upstream/main", upstream)

    _git(repo, "checkout", "-b", "axiom")
    current = _commit(
        repo,
        "fork.txt",
        "replayed carry\n",
        "feat(fork): preserved carry from regenerated candidate",
    )
    _git(repo, "update-ref", "refs/remotes/origin/axiom", current)

    hermes_home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    update_cmd._print_reconciliation_receipt(
        repo=repo,
        previous_sha=previous,
        branch="axiom",
        git_cmd=["git"],
    )

    out = capsys.readouterr().out
    assert "━━ Reconciliation ━━" in out
    assert "Branch" in out and "axiom" in out
    assert previous[:10] in out
    assert current[:10] in out
    assert upstream[:10] in out
    assert "✓ current matches origin/axiom" in out
    assert "✓ upstream/main is merged into current" in out
    assert "━━ Upstream changes included ━━" in out
    assert "1 features, 1 fixes" in out
    assert "feat(cli): improve update output" in out
    assert "fix(update): clarify completion" in out
    assert "preserved carry from regenerated candidate" not in out
    assert old_upstream[:10] not in out

    brief = hermes_home / "logs" / "last-update-brief.md"
    assert brief.exists()
    body = brief.read_text(encoding="utf-8")
    assert "feat(cli): improve update output" in body
    assert "preserved carry from regenerated candidate" not in body
