import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from hermes_cli import update_ui


class _FakeRawTTY:
    def __init__(self):
        self.writes = []

    def isatty(self):
        return True

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass


class _WrappedStdout:
    def __init__(self, raw):
        self._original = raw
        self.writes = []

    def isatty(self):
        return True

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass


def test_status_line_preserves_scrollback_and_uses_no_raw_tty_control(monkeypatch):
    raw = _FakeRawTTY()
    wrapped = _WrappedStdout(raw)
    monkeypatch.setattr(update_ui.sys, "stdout", wrapped)

    status = update_ui.StatusLine(interval=0.001)
    status.start("resolve: agent resolve")
    status.update("resolve: validate")
    status.success(note="resolved handoff")

    raw_output = "".join(raw.writes)
    wrapped_output = "".join(wrapped.writes)

    assert raw_output == ""
    assert wrapped_output == (
        "⏳ resolve: agent resolve\n"
        "⏳ resolve: validate\n"
        "✓ resolved handoff\n"
    )
    assert "\r" not in wrapped_output
    assert "\033" not in wrapped_output
    assert not any(frame in wrapped_output for frame in update_ui._SPINNER_FRAMES)


def test_digest_header_can_include_commit_sha_range():
    commits = [
        ("abcdef123456", "feat(update): add digest range", "Bailey", "feat"),
        ("123456abcdef", "fix(update): wire final summary", "Bailey", "fix"),
    ]
    buckets = update_ui._bucket_commits(commits)

    digest = update_ui._render_digest(
        commits,
        buckets,
        "2 files changed",
        title="Upstream changes since last update",
        range_label="abcdef1234..123456abcd",
    )

    assert "━━ Upstream changes since last update (abcdef1234..123456abcd) ━━" in digest
    assert "Features (1):" in digest
    assert "Fixes (1):" in digest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cp1252 regression")
def test_collect_commits_decodes_utf8_git_output_under_cp1252(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args, env=None):
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            env=env,
            check=True,
            capture_output=True,
        )

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "ASCII Author")
    (repo / "fixture.txt").write_text("first\n", encoding="utf-8")
    git("add", "fixture.txt")
    git("commit", "-m", "test: first commit")
    old_sha = git("rev-parse", "HEAD").stdout.decode("ascii").strip()

    (repo / "fixture.txt").write_text("second\n", encoding="utf-8")
    git("add", "fixture.txt")
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "李灵航",
        "GIT_AUTHOR_EMAIL": "unicode@example.com",
        "GIT_COMMITTER_NAME": "李灵航",
        "GIT_COMMITTER_EMAIL": "unicode@example.com",
    }
    git("commit", "-m", "fix(update): preserve Unicode attribution", env=commit_env)
    new_sha = git("rev-parse", "HEAD").stdout.decode("ascii").strip()

    project_root = Path(update_ui.__file__).resolve().parents[1]
    probe = textwrap.dedent(
        f"""
        import json
        from pathlib import Path
        from hermes_cli.update_ui import _collect_commits

        commits, _stat, _files = _collect_commits(
            Path({str(repo)!r}), {old_sha!r}, {new_sha!r}
        )
        assert commits, "Unicode git log output was lost"
        print(json.dumps(commits, ensure_ascii=True))
        """
    )
    child_env = {
        **os.environ,
        "PYTHONUTF8": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(project_root),
    }
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=project_root,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr
    commits = json.loads(result.stdout)
    assert commits[0][2] == "李灵航"
    assert "UnicodeDecodeError" not in result.stderr
