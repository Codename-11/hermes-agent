from __future__ import annotations

import subprocess
from pathlib import Path

from hermes_cli import update_cmd


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_desktop_only_update_does_not_require_python_dependency_refresh(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    desktop = tmp_path / "apps" / "desktop" / "main.ts"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("export const value = 1;\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")
    old_head = _git(tmp_path, "rev-parse", "HEAD")

    desktop.write_text("export const value = 2;\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "desktop only")

    checker = getattr(update_cmd, "_python_dependency_refresh_required", None)
    assert callable(checker), "update path must expose the dependency refresh decision"
    assert checker(["git"], tmp_path, old_head) is False


def test_pyproject_change_requires_python_dependency_refresh(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")
    old_head = _git(tmp_path, "rev-parse", "HEAD")

    pyproject.write_text(
        "[project]\nname = 'demo'\ndependencies = ['httpx']\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "dependencies")

    assert update_cmd._python_dependency_refresh_required(["git"], tmp_path, old_head) is True


def test_unknown_update_base_requires_python_dependency_refresh(tmp_path: Path) -> None:
    assert update_cmd._python_dependency_refresh_required(["git"], tmp_path, None) is True
