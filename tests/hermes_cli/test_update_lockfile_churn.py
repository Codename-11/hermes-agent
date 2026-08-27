"""Regression coverage for updater-owned npm lockfile rewrites."""

from __future__ import annotations

import json
import subprocess

from hermes_cli import main as cli_main
from hermes_cli import update_cmd


def _dump(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2) + "\n").encode()


def _base_lock() -> dict:
    return {
        "name": "hermes-agent",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "hermes-agent", "dependencies": {"app": "1.0.0"}},
            "node_modules/app": {
                "version": "1.0.0",
                "resolved": "https://registry.invalid/app.tgz",
                "integrity": "sha512-app",
                "license": "MIT",
                "peer": True,
            },
            "node_modules/encoding": {
                "version": "0.1.13",
                "resolved": "https://registry.invalid/encoding.tgz",
                "integrity": "sha512-encoding",
                "license": "MIT",
                "optional": True,
                "dependencies": {"iconv-lite": "^0.6.2"},
            },
        },
    }


def test_lockfile_churn_accepts_annotations_and_optional_transitive_omission():
    before = _base_lock()
    after = _base_lock()
    after["packages"]["node_modules/app"].pop("peer")
    after["packages"]["node_modules/app"]["license"] = "Apache-2.0"
    after["packages"].pop("node_modules/encoding")

    assert update_cmd._lockfile_churn_is_incidental(_dump(before), _dump(after))


def test_lockfile_churn_rejects_dependency_version_change():
    before = _base_lock()
    after = _base_lock()
    after["packages"]["node_modules/app"]["version"] = "2.0.0"

    assert not update_cmd._lockfile_churn_is_incidental(_dump(before), _dump(after))


def test_restore_incidental_lockfile_churn_preserves_prebuild_bytes(tmp_path):
    lock_path = tmp_path / "package-lock.json"
    package_path = tmp_path / "package.json"
    package_before = b'{"name":"hermes-agent"}\n'
    lock_before = _dump(_base_lock())
    after = _base_lock()
    after["packages"]["node_modules/app"].pop("peer")
    lock_path.write_bytes(_dump(after))
    package_path.write_bytes(package_before)

    assert update_cmd._restore_incidental_lockfile_churn(
        lock_path,
        package_path,
        lock_before=lock_before,
        package_before=package_before,
    )
    assert lock_path.read_bytes() == lock_before


def test_restore_incidental_lockfile_churn_leaves_meaningful_change(tmp_path):
    lock_path = tmp_path / "package-lock.json"
    package_path = tmp_path / "package.json"
    package_before = b'{"name":"hermes-agent"}\n'
    lock_before = _dump(_base_lock())
    after = _base_lock()
    after["packages"]["node_modules/app"]["version"] = "2.0.0"
    changed = _dump(after)
    lock_path.write_bytes(changed)
    package_path.write_bytes(package_before)

    assert not update_cmd._restore_incidental_lockfile_churn(
        lock_path,
        package_path,
        lock_before=lock_before,
        package_before=package_before,
    )
    assert lock_path.read_bytes() == changed


def test_desktop_rebuild_restores_incidental_root_lockfile_churn(
    tmp_path, monkeypatch
):
    desktop_dir = tmp_path / "apps" / "desktop"
    desktop_dir.mkdir(parents=True)
    (desktop_dir / "package.json").write_text("{}", encoding="utf-8")
    package_path = tmp_path / "package.json"
    package_before = b'{"name":"hermes-agent"}\n'
    package_path.write_bytes(package_before)
    lock_path = tmp_path / "package-lock.json"
    lock_before = _dump(_base_lock())
    lock_path.write_bytes(lock_before)

    churned = _base_lock()
    churned["packages"]["node_modules/app"].pop("peer")

    def build(*_args, **_kwargs):
        lock_path.write_bytes(_dump(churned))
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(cli_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli_main, "_resolve_node_runtime_npm", lambda: "npm.cmd")
    monkeypatch.setattr(cli_main, "_desktop_build_needed", lambda *_a, **_k: True)
    monkeypatch.setattr(cli_main, "_run_logged_subprocess", build)

    assert update_cmd._rebuild_desktop_after_update(
        desktop_dir, had_desktop_app_before_update=True
    )
    assert lock_path.read_bytes() == lock_before


def test_discard_lockfile_churn_restores_only_verified_incidental_diff(tmp_path):
    package_path = tmp_path / "package.json"
    lock_path = tmp_path / "package-lock.json"
    package_path.write_text('{"name":"hermes-agent"}\n', encoding="utf-8")
    before = _base_lock()
    lock_path.write_bytes(_dump(before))

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    git("init")
    git("add", "package.json", "package-lock.json")
    git(
        "-c", "user.name=Hermes Test",
        "-c", "user.email=hermes@example.invalid",
        "commit", "-m", "fixture",
    )

    incidental = _base_lock()
    incidental["packages"]["node_modules/app"].pop("peer")
    lock_path.write_bytes(_dump(incidental))
    update_cmd._discard_lockfile_churn(["git"], tmp_path)
    assert json.loads(lock_path.read_text(encoding="utf-8")) == before

    meaningful = _base_lock()
    meaningful["packages"]["node_modules/app"]["version"] = "2.0.0"
    changed = _dump(meaningful)
    lock_path.write_bytes(changed)
    update_cmd._discard_lockfile_churn(["git"], tmp_path)
    assert lock_path.read_bytes() == changed
