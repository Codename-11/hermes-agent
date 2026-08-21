"""Regression: ZIP fallback must preserve Desktop build artifacts.

The ZIP fallback update path replaces top-level directories (``apps/``,
``hermes_cli/``, etc.) by rmtree + copytree from the upstream source ZIP.
Build artifacts like ``apps/desktop/release/win-unpacked/Hermes.exe`` don't
exist in source; they're local electron-builder output.  Without the
backup/restore guard the ZIP fallback destroys the Desktop launcher and
leaves shortcuts pointing at nothing.

Observed 2026-06-16 and 2026-06-17 on Windows.
"""
import os
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _make_fake_zip(zip_path: Path, branch: str = "main") -> None:
    """Create a minimal ZIP mimicking a GitHub source archive."""
    prefix = f"hermes-agent-{branch}"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{prefix}/hermes_cli/__init__.py", "# source\n")
        zf.writestr(f"{prefix}/apps/desktop/package.json", "{}\n")
        zf.writestr(f"{prefix}/pyproject.toml", "[project]\nname='hermes-agent'\nversion='0.0.0'\n")


def test_zip_fallback_preserves_desktop_release(tmp_path, monkeypatch):
    """Desktop release artifacts survive the ZIP fallback extraction."""
    from hermes_cli import main as main_mod

    project = tmp_path / "project"
    project.mkdir()

    # Pre-existing Desktop build artifact
    release = project / "apps" / "desktop" / "release" / "win-unpacked"
    release.mkdir(parents=True)
    exe = release / "Hermes.exe"
    exe.write_bytes(b"FAKE_EXE")
    marker = release / "resources" / "app.asar"
    marker.parent.mkdir(parents=True)
    marker.write_text("asar-data")

    # Pre-existing source file that should be replaced
    old_init = project / "hermes_cli" / "__init__.py"
    old_init.parent.mkdir(parents=True)
    old_init.write_text("# old")

    fake_zip = tmp_path / "update.zip"
    _make_fake_zip(fake_zip, branch="main")

    monkeypatch.setattr(main_mod, "PROJECT_ROOT", project)

    # Stub out everything after extraction so we only test the copy logic.
    monkeypatch.setattr(main_mod, "_clear_bytecode_cache", lambda _root: 0)

    # Mock urlretrieve to copy our fake zip instead of downloading
    def fake_urlretrieve(url, dest):
        shutil.copy2(str(fake_zip), dest)
        return dest, {}

    # Stub out post-extraction steps that import heavy deps
    monkeypatch.setattr(main_mod, "_install_python_dependencies_with_optional_fallback", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_update_node_dependencies", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_build_web_ui", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_resolve_update_branch", lambda *a, **k: "main")

    with patch("hermes_cli.main.urlretrieve", fake_urlretrieve, create=True):
        with patch("urllib.request.urlretrieve", fake_urlretrieve):
            with patch("hermes_cli.managed_uv.ensure_uv", return_value=None):
                with patch("hermes_cli.managed_uv.update_managed_uv"):
                    try:
                        main_mod._update_via_zip(SimpleNamespace(branch="main"))
                    except SystemExit:
                        pass  # may exit on downstream stubs

    # The source file should be updated from the ZIP
    assert (project / "hermes_cli" / "__init__.py").read_text() == "# source\n"

    # Desktop build artifact must survive
    assert exe.exists(), "Hermes.exe was destroyed by ZIP fallback"
    assert exe.read_bytes() == b"FAKE_EXE"
    assert marker.exists(), "app.asar was destroyed by ZIP fallback"
