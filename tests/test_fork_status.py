from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


SCRIPT = Path(__file__).parents[1] / "scripts" / "fork-status.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fork_status", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jobs(path: Path, jobs: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"jobs": jobs}))


def test_distinguishes_shared_drift_watch_from_paused_reconciliation(tmp_path: Path) -> None:
    module = load_module()
    shared = tmp_path / "shared.json"
    legacy = tmp_path / "sentinel.json"
    write_jobs(
        shared,
        [
            {
                "id": "watch-id",
                "name": "Hermes Daily Check",
                "owner_profile": "victor",
                "enabled": True,
                "state": "scheduled",
            },
            {
                "id": "sync-id",
                "name": "Hermes Axiom Sync",
                "owner_profile": "sentinel",
                "enabled": False,
                "state": "paused",
            },
        ],
    )
    write_jobs(legacy, [])
    setattr(module, "SHARED_CRON", shared)
    setattr(module, "SENTINEL_CRON", legacy)

    assert module.drift_watch_state() == {
        "found": True,
        "path": str(shared),
        "id": "watch-id",
        "name": "Hermes Daily Check",
        "owner_profile": "victor",
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "last_run_at": None,
        "last_status": None,
        "schedule": None,
        "deliver": None,
    }
    assert module.sentinel_sync_state()["state"] == "paused"


def test_falls_back_to_profile_registry_after_shared_parse_error(tmp_path: Path) -> None:
    module = load_module()
    shared = tmp_path / "shared.json"
    legacy = tmp_path / "sentinel.json"
    shared.write_text("not json")
    write_jobs(
        legacy,
        [
            {
                "id": "legacy-sync",
                "name": "Hermes Axiom Sync",
                "owner_profile": "sentinel",
                "enabled": False,
                "state": "paused",
            }
        ],
    )
    setattr(module, "SHARED_CRON", shared)
    setattr(module, "SENTINEL_CRON", legacy)

    state = module.sentinel_sync_state()

    assert state["found"] is True
    assert state["id"] == "legacy-sync"
    assert state["path"] == str(legacy)
