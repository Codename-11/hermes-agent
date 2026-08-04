"""Tests for _verify_console_scripts_installed (issue #52931)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_pyproject(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
        [project]
        name = "fake"
        version = "0.0.0"

        [project.scripts]
        hermes = "hermes_cli.main:main"
        hermes-agent = "run_agent:main"
        hermes-acp = "acp_adapter.entry:main"
    """
        )
    )
    import hermes_cli.main as main_mod

    monkeypatch.setattr(main_mod, "PROJECT_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def fake_scripts_dir(tmp_path):
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    return scripts


class TestVerifyConsoleScriptsInstalled:
    def test_no_action_when_all_shims_present(self, temp_pyproject, fake_scripts_dir):
        for name in ("hermes", "hermes-agent", "hermes-acp"):
            (fake_scripts_dir / f"{name}.exe").write_bytes(b"fake")

        with patch("hermes_cli.main._is_windows", return_value=True), \
             patch("hermes_cli.main._venv_scripts_dir", return_value=fake_scripts_dir), \
             patch("hermes_cli.main._run_quarantined_install") as mock_install:
            from hermes_cli.main import _verify_console_scripts_installed

            _verify_console_scripts_installed(["uv", "pip"], env={})

        mock_install.assert_not_called()

    def test_falls_back_to_venv_pip_when_uv_leaves_shims_missing(
        self, temp_pyproject, fake_scripts_dir
    ):
        python_exe = fake_scripts_dir / "python.exe"
        python_exe.write_bytes(b"fake")
        calls = []

        def fake_install(cmd, *, env, scripts_dir):
            calls.append(cmd)
            if cmd[0] == str(python_exe):
                for name in ("hermes", "hermes-agent", "hermes-acp"):
                    (fake_scripts_dir / f"{name}.exe").write_bytes(b"fake")

        with patch("hermes_cli.main._is_windows", return_value=True), \
             patch("hermes_cli.main._venv_scripts_dir", return_value=fake_scripts_dir), \
             patch("hermes_cli.main._run_quarantined_install", side_effect=fake_install):
            from hermes_cli.main import _verify_console_scripts_installed

            _verify_console_scripts_installed(["uv", "pip"], env={"VIRTUAL_ENV": "fake"})

        assert calls == [
            ["uv", "pip", "install", "--reinstall", "-e", "."],
            [
                str(python_exe),
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                "--no-deps",
                "-e",
                ".",
            ],
        ]

    def test_raises_when_uv_and_pip_leave_shims_missing(
        self, temp_pyproject, fake_scripts_dir
    ):
        (fake_scripts_dir / "python.exe").write_bytes(b"fake")

        with patch("hermes_cli.main._is_windows", return_value=True), \
             patch("hermes_cli.main._venv_scripts_dir", return_value=fake_scripts_dir), \
             patch("hermes_cli.main._run_quarantined_install"):
            from hermes_cli.main import _verify_console_scripts_installed

            with pytest.raises(RuntimeError, match="console entry points remain missing"):
                _verify_console_scripts_installed(["uv", "pip"], env={})




    def test_quarantine_shims_include_declared_console_scripts(
        self, temp_pyproject, fake_scripts_dir
    ):
        import hermes_cli.main as main_mod

        with patch("hermes_cli.main._is_windows", return_value=True):
            names = {path.name for path in main_mod._hermes_exe_shims(fake_scripts_dir)}

        assert {"hermes.exe", "hermes-agent.exe", "hermes-acp.exe"} <= names
        assert "hermes-gateway.exe" in names
