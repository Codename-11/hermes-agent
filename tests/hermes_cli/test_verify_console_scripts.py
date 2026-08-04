"""Tests for _verify_console_scripts_installed (issue #52931)."""

from __future__ import annotations

import subprocess
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
    def test_ensure_venv_pip_bootstraps_when_probe_fails(self, tmp_path):
        python_exe = tmp_path / "python.exe"
        python_exe.write_bytes(b"fake")
        probe = subprocess.CompletedProcess([], 1)
        bootstrapped = subprocess.CompletedProcess([], 0)
        verified = subprocess.CompletedProcess([], 0)

        with patch(
            "hermes_cli.main.subprocess.run",
            side_effect=[probe, bootstrapped, verified],
        ) as mock_run:
            from hermes_cli.main import _ensure_venv_pip

            _ensure_venv_pip(python_exe, env={"VIRTUAL_ENV": "fake"})

        assert mock_run.call_args_list[0].args[0] == [
            str(python_exe), "-m", "pip", "--version"
        ]
        assert mock_run.call_args_list[0].kwargs["check"] is False
        assert mock_run.call_args_list[1].args[0] == [
            str(python_exe), "-m", "ensurepip", "--upgrade"
        ]
        assert mock_run.call_args_list[1].kwargs["check"] is True
        assert mock_run.call_args_list[2].args[0] == [
            str(python_exe), "-m", "pip", "--version"
        ]
        assert mock_run.call_args_list[2].kwargs["check"] is True

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
        events = []

        def fake_install(cmd, *, env, scripts_dir):
            calls.append(cmd)
            events.append("install")
            if cmd[0] == str(python_exe):
                for name in ("hermes", "hermes-agent", "hermes-acp"):
                    (fake_scripts_dir / f"{name}.exe").write_bytes(b"fake")

        with patch("hermes_cli.main._is_windows", return_value=True), \
             patch("hermes_cli.main._venv_scripts_dir", return_value=fake_scripts_dir), \
             patch("hermes_cli.main._run_quarantined_install", side_effect=fake_install), \
             patch(
                 "hermes_cli.main._ensure_venv_pip",
                 side_effect=lambda *_a, **_k: events.append("ensure-pip"),
             ):
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
        assert events == ["install", "ensure-pip", "install"]

    def test_raises_when_uv_and_pip_leave_shims_missing(
        self, temp_pyproject, fake_scripts_dir
    ):
        (fake_scripts_dir / "python.exe").write_bytes(b"fake")

        with patch("hermes_cli.main._is_windows", return_value=True), \
             patch("hermes_cli.main._venv_scripts_dir", return_value=fake_scripts_dir), \
             patch("hermes_cli.main._run_quarantined_install"), \
             patch("hermes_cli.main._ensure_venv_pip"):
            from hermes_cli.main import _verify_console_scripts_installed

            with pytest.raises(RuntimeError, match="console entry points remain missing"):
                _verify_console_scripts_installed(["uv", "pip"], env={})

    def test_propagates_failed_pip_fallback_even_if_it_creates_shims(
        self, temp_pyproject, fake_scripts_dir
    ):
        python_exe = fake_scripts_dir / "python.exe"
        python_exe.write_bytes(b"fake")

        def fake_install(cmd, *, env, scripts_dir):
            if cmd[0] == str(python_exe):
                for name in ("hermes", "hermes-agent", "hermes-acp"):
                    (fake_scripts_dir / f"{name}.exe").write_bytes(b"fake")
                raise subprocess.CalledProcessError(7, cmd)

        with patch("hermes_cli.main._is_windows", return_value=True), \
             patch("hermes_cli.main._venv_scripts_dir", return_value=fake_scripts_dir), \
             patch("hermes_cli.main._run_quarantined_install", side_effect=fake_install), \
             patch("hermes_cli.main._ensure_venv_pip"):
            from hermes_cli.main import _verify_console_scripts_installed

            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                _verify_console_scripts_installed(["uv", "pip"], env={})

        assert exc_info.value.returncode == 7




    def test_quarantine_shims_include_declared_console_scripts(
        self, temp_pyproject, fake_scripts_dir
    ):
        import hermes_cli.main as main_mod

        with patch("hermes_cli.main._is_windows", return_value=True):
            names = {path.name for path in main_mod._hermes_exe_shims(fake_scripts_dir)}

        assert {"hermes.exe", "hermes-agent.exe", "hermes-acp.exe"} <= names
        assert "hermes-gateway.exe" in names
