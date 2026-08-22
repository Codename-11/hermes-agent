from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "scripts" / "fork_carry_replay.py"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Replay Tests")
    git(repo, "config", "user.email", "replay@example.test")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "feature.txt").write_text("base\n", encoding="utf-8")
    (repo / "tests" / "test_feature.py").write_text("pass\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "feature.txt").write_text("base\ncarry\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "carry")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "carry/example", commit)
    git(repo, "reset", "--hard", base)
    return repo, base, commit


def manifest(base: str, commit: str, *, marker: Path | None = None) -> dict[str, object]:
    command = [sys.executable, "-c", "pass"]
    if marker is not None:
        command = [
            sys.executable,
            "-c",
            "import os, sys; from pathlib import Path; assert sys.argv[1] == 'literal; & |'; Path(os.environ['MARKER']).touch()",
            "literal; & |",
        ]
    return {
        "schema_version": 1,
        "carries": [
            {
                "id": "declaration-only",
                "order": 10,
                "title": "Declaration only",
                "status": "active",
                "domain_id": "example",
                "ownership": "core",
                "contract": {"path": "FORK.md", "heading": "Example"},
                "depends_on": [],
                "provenance": [{"kind": "manual", "description": "test"}],
                "summary": "Not extracted yet.",
                "paths": ["src/feature.txt"],
                "tests": ["tests/test_feature.py"],
                "checks": [{"id": "declaration-check", "cwd": ".", "argv": [sys.executable, "-c", "pass"], "env": {}, "covers": ["tests/test_feature.py"]}],
                "retirement": "When upstream owns it.",
            },
            {
                "id": "example-carry",
                "order": 20,
                "title": "Example carry",
                "status": "active",
                "domain_id": "example",
                "ownership": "core",
                "contract": {"path": "FORK.md", "heading": "Example"},
                "depends_on": ["declaration-only"],
                "provenance": [{"kind": "commit", "repository": "test/repo", "revision": commit}],
                "summary": "Replay extracted carry.",
                "paths": ["src/feature.txt"],
                "tests": ["tests/test_feature.py"],
                "checks": [{"id": "example-check", "cwd": ".", "argv": command, "env": {"MARKER": str(marker) if marker else "unused"}, "covers": ["tests/test_feature.py"]}],
                "retirement": "When upstream owns it.",
                "replay": {"kind": "commit_series", "source_ref": "carry/example", "base_commit": base, "commits": [commit]},
            },
        ],
    }


def write_manifest(repo: Path, value: dict[str, object]) -> Path:
    path = repo / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=repo, text=True, capture_output=True, check=False)


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fork_carry_replay", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_is_deterministic_and_distinguishes_incomplete(tmp_path: Path) -> None:
    repo, base, commit = make_repo(tmp_path)
    path = write_manifest(repo, manifest(base, commit))
    first = run(repo, "plan", "--manifest", str(path), "--json")
    second = run(repo, "plan", "--manifest", str(path), "--json")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert [row["id"] for row in report["replay_ready"]] == ["example-carry"]
    assert report["incomplete_active"] == ["declaration-only"]
    assert report["replay_ready"][0]["base_commit"] == base
    assert report["replay_ready"][0]["commits"] == [commit]


def test_probe_replays_commit_without_running_checks_or_mutating_source(tmp_path: Path) -> None:
    repo, base, commit = make_repo(tmp_path)
    marker = tmp_path / "marker"
    path = write_manifest(repo, manifest(base, commit, marker=marker))
    branch_before = git(repo, "rev-parse", "main")
    result = run(repo, "probe", "--manifest", str(path), "--carry", "example-carry", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "success"
    assert len(report["applied_commits"]) == 1
    assert report["checks"] == []
    assert not marker.exists()
    assert git(repo, "rev-parse", "main") == branch_before == base
    assert report["worktree_removed"] is True
    assert not Path(report["worktree"]).exists()


def test_probe_checks_are_opt_in_and_argv_is_shell_safe(tmp_path: Path) -> None:
    repo, base, commit = make_repo(tmp_path)
    marker = tmp_path / "marker ; not a command"
    path = write_manifest(repo, manifest(base, commit, marker=marker))
    result = run(repo, "probe", "--manifest", str(path), "--carry", "example-carry", "--run-checks", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert marker.exists()
    assert report["checks"][0]["id"] == "example-check"
    assert report["checks"][0]["returncode"] == 0


def test_probe_conflict_fails_and_cleans_up(tmp_path: Path) -> None:
    repo, base, commit = make_repo(tmp_path)
    git(repo, "checkout", "-b", "conflict", base)
    (repo / "src" / "feature.txt").write_text("conflicting\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "conflicting base")
    conflicting_base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    path = write_manifest(repo, manifest(base, commit))
    result = run(repo, "probe", "--manifest", str(path), "--carry", "example-carry", "--base", conflicting_base, "--json")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "apply_failed"
    assert report["conflicts"] == ["src/feature.txt"]
    assert report["worktree_removed"] is True
    assert not Path(report["worktree"]).exists()


def test_explicit_newer_base_probe_applies(tmp_path: Path) -> None:
    repo, base, commit = make_repo(tmp_path)
    git(repo, "checkout", "-b", "newer", base)
    (repo / "unrelated.txt").write_text("newer\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "newer base")
    newer = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    path = write_manifest(repo, manifest(base, commit))
    result = run(repo, "probe", "--manifest", str(path), "--carry", "example-carry", "--base", newer, "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["base_mode"] == "explicit"


def test_default_base_requires_first_parent_match(tmp_path: Path) -> None:
    repo, base, commit = make_repo(tmp_path)
    value = manifest("0" * 40, commit)
    path = write_manifest(repo, value)
    result = run(repo, "probe", "--manifest", str(path), "--carry", "example-carry", "--json")
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] in {"missing_object", "base_mismatch"}


def test_keep_preserves_worktree(tmp_path: Path) -> None:
    repo, base, commit = make_repo(tmp_path)
    path = write_manifest(repo, manifest(base, commit))
    scratch = tmp_path / "scratch"
    result = run(repo, "probe", "--manifest", str(path), "--carry", "example-carry", "--scratch-root", str(scratch), "--keep", "--json")
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["worktree_removed"] is False
    worktree = Path(report["worktree"])
    assert worktree.exists()
    git(repo, "worktree", "remove", "--force", str(worktree))


def test_probe_rejects_declaration_only_and_bad_manifest(tmp_path: Path) -> None:
    repo, base, commit = make_repo(tmp_path)
    value = manifest(base, commit)
    path = write_manifest(repo, value)
    result = run(repo, "probe", "--manifest", str(path), "--carry", "declaration-only", "--json")
    assert result.returncode == 1
    value["carries"][1]["replay"]["commits"] = []  # type: ignore[index]
    write_manifest(repo, value)
    malformed = run(repo, "plan", "--manifest", str(path), "--json")
    assert malformed.returncode == 1
    assert json.loads(malformed.stdout)["valid"] is False


def test_import_is_side_effect_free(capsys: pytest.CaptureFixture[str]) -> None:
    load_module()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_git_commands_enable_windows_long_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    observed: list[str] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.extend(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module._run_git(tmp_path, "status")
    assert observed[:3] == ["git", "-c", "core.longpaths=true"]
