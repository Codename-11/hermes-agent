"""Regression tests for shared cron storage with profile-scoped execution.

Cron jobs are stored in one root registry for visibility/management, but every
job carries an owner profile. Gateways and user-facing cron commands filter by
that owner so one profile cannot execute or mutate another profile's jobs.
"""
import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _reload_jobs(root: Path, hermes_home: Path, monkeypatch):
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: root)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    import cron.jobs as jobs
    importlib.reload(jobs)
    return jobs


def test_cron_storage_shared_root_but_owner_scoped_under_profile(tmp_path, monkeypatch):
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "sentinel"
    profile_home.mkdir(parents=True)

    jobs = _reload_jobs(root, profile_home, monkeypatch)
    try:
        assert jobs.HERMES_DIR.resolve() == root.resolve()
        assert jobs.JOBS_FILE.resolve() == (root / "cron" / "jobs.json").resolve()
        assert jobs.get_active_cron_profile() == "sentinel"

        created = jobs.create_job(prompt="x", schedule="30m", name="sentinel job")
        assert created["owner_profile"] == "sentinel"
        assert created["scope"] == "profile"
        assert jobs.list_jobs(include_disabled=True)[0]["id"] == created["id"]

        monkeypatch.setenv("HERMES_HOME", str(root))
        assert jobs.get_active_cron_profile() == "default"
        assert jobs.list_jobs(include_disabled=True) == []
        assert jobs.get_job(created["id"]) is None
        assert jobs.get_job(created["id"], include_all_profiles=True)["owner_profile"] == "sentinel"
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_cron_storage_unaffected_when_no_profile(tmp_path, monkeypatch):
    root = tmp_path / "hermes_home"
    root.mkdir(parents=True)

    jobs = _reload_jobs(root, root, monkeypatch)
    try:
        assert jobs.JOBS_FILE.resolve() == (root / "cron" / "jobs.json").resolve()
        created = jobs.create_job(prompt="x", schedule="30m", name="root job")
        assert created["owner_profile"] == "default"
        assert jobs.list_jobs(include_disabled=True)[0]["id"] == created["id"]
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_tick_lock_uses_shared_root_store(tmp_path, monkeypatch):
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "p"
    profile_home.mkdir(parents=True)
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: root)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    import cron.scheduler as sched
    importlib.reload(sched)
    try:
        sched._hermes_home = None
        lock_dir, lock_file = sched._get_lock_paths()
        assert lock_dir.resolve() == (root / "cron").resolve()
        assert lock_file.resolve() == (root / "cron" / ".tick.lock").resolve()
        assert lock_dir.resolve() != (profile_home / "cron").resolve()
    finally:
        monkeypatch.undo()
        importlib.reload(sched)


def test_due_jobs_filtered_by_owner_profile(tmp_path, monkeypatch):
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "sentinel"
    profile_home.mkdir(parents=True)
    jobs = _reload_jobs(root, profile_home, monkeypatch)
    try:
        root_cron = root / "cron"
        root_cron.mkdir(parents=True)
        due_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        payload = {
            "jobs": [
                {
                    "id": "aaaaaaaaaaaa",
                    "name": "root due",
                    "prompt": "root",
                    "schedule": {"kind": "once", "run_at": due_at},
                    "schedule_display": "once",
                    "next_run_at": due_at,
                    "enabled": True,
                    "owner_profile": "default",
                    "scope": "profile",
                },
                {
                    "id": "bbbbbbbbbbbb",
                    "name": "sentinel due",
                    "prompt": "sentinel",
                    "schedule": {"kind": "once", "run_at": due_at},
                    "schedule_display": "once",
                    "next_run_at": due_at,
                    "enabled": True,
                    "owner_profile": "sentinel",
                    "scope": "profile",
                },
            ]
        }
        (root_cron / "jobs.json").write_text(json.dumps(payload), encoding="utf-8")
        due = jobs.get_due_jobs()
        assert [j["id"] for j in due] == ["bbbbbbbbbbbb"]
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_legacy_profile_store_imports_once_into_shared_root(tmp_path, monkeypatch):
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "sentinel"
    (profile_home / "cron").mkdir(parents=True)
    (root / "cron").mkdir(parents=True)

    (root / "cron" / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": "rootjob00001", "name": "root", "prompt": "x"}]}),
        encoding="utf-8",
    )
    (profile_home / "cron" / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": "sentjob00001", "name": "sentinel", "prompt": "x"}]}),
        encoding="utf-8",
    )

    jobs = _reload_jobs(root, profile_home, monkeypatch)
    try:
        all_jobs = jobs.load_jobs()
        by_id = {j["id"]: j for j in all_jobs}
        assert by_id["rootjob00001"]["owner_profile"] == "default"
        assert by_id["sentjob00001"]["owner_profile"] == "sentinel"
        assert [j["id"] for j in jobs.list_jobs(include_disabled=True)] == ["sentjob00001"]

        legacy_payload = json.loads((profile_home / "cron" / "jobs.json").read_text(encoding="utf-8"))
        assert legacy_payload["migrated_to_shared_store"] is True
        assert legacy_payload["jobs"] == []
        backups = list((profile_home / "cron").glob("jobs.pre-shared-store-migration.*.json"))
        assert backups
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_run_job_uses_owner_profile_home_for_scripts(tmp_path, monkeypatch):
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "sentinel"
    scripts_dir = profile_home / "scripts"
    scripts_dir.mkdir(parents=True)
    script = scripts_dir / "probe.py"
    script.write_text(
        "import os\nprint(os.environ['HERMES_HOME'], end='')\n",
        encoding="utf-8",
    )

    jobs = _reload_jobs(root, root, monkeypatch)
    import cron.scheduler as sched
    importlib.reload(sched)
    try:
        ok, _doc, final_response, err = sched.run_job({
            "id": "ownerhome001",
            "name": "owner home",
            "script": "probe.py",
            "no_agent": True,
            "owner_profile": "sentinel",
            "scope": "profile",
        })
        assert ok is True
        assert err is None
        assert final_response == str(profile_home)
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)
        importlib.reload(sched)


def test_get_default_hermes_root_still_resolves_profile_parent(tmp_path, monkeypatch):
    import hermes_constants
    native = tmp_path / "native_home"
    monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: native)

    monkeypatch.setenv("HERMES_HOME", "/opt/data")
    assert hermes_constants.get_default_hermes_root() == Path("/opt/data")

    monkeypatch.setenv("HERMES_HOME", "/opt/data/profiles/coder")
    assert hermes_constants.get_default_hermes_root() == Path("/opt/data")


# ---------------------------------------------------------------------------
# Per-job profile EXECUTION scoping (#32091 follow-up).
#
# The storage half of #32091 (above) moved every profile's jobs into one shared
# root store. But a job must still EXECUTE under its owning profile's
# environment (.env / config.yaml / credentials) — not whichever profile's
# ticker picks it up. These tests cover the execution-scoping half.
# ---------------------------------------------------------------------------


def _profile_env(tmp_path, monkeypatch, active="default"):
    """Set up a root home with a 'donna' profile dir and point the platform
    default at it. Returns (root, donna_home). ``active`` selects which
    HERMES_HOME the process runs under."""
    root = tmp_path / "hermes_home"
    (root / "cron").mkdir(parents=True)
    donna_home = root / "profiles" / "donna"
    (donna_home / "cron").mkdir(parents=True)
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home",
                        lambda: root)
    monkeypatch.setenv("HERMES_HOME", str(root if active == "default" else donna_home))
    return root, donna_home


def test_create_job_autocaptures_active_profile(tmp_path, monkeypatch):
    """A job created from inside a profile session is tagged with that profile,
    so the scheduler can later scope its execution back to it."""
    root, donna_home = _profile_env(tmp_path, monkeypatch, active="donna")
    import cron.jobs as jobs
    importlib.reload(jobs)
    try:
        job = jobs.create_job(prompt="audit", schedule="every 1h", name="a")
        # auto-captured from the active (donna) session
        assert job["profile"] == "donna"
        # and it landed in the SHARED ROOT store, not donna's profile-local one
        assert jobs.JOBS_FILE.resolve() == (root / "cron" / "jobs.json").resolve()
        assert jobs.JOBS_FILE.exists()
        assert not (donna_home / "cron" / "jobs.json").exists()
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_create_job_explicit_profile_override(tmp_path, monkeypatch):
    """An explicit profile= wins over the auto-captured active profile."""
    root, donna_home = _profile_env(tmp_path, monkeypatch, active="default")
    (root / "profiles" / "ops" / "cron").mkdir(parents=True)
    import cron.jobs as jobs
    importlib.reload(jobs)
    try:
        job = jobs.create_job(prompt="x", schedule="every 2h", profile="ops")
        assert job["profile"] == "ops"
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_other_profile_cannot_claim_shared_job(tmp_path, monkeypatch):
    root, donna_home = _profile_env(tmp_path, monkeypatch, active="default")
    import cron.jobs as jobs
    importlib.reload(jobs)
    try:
        created = jobs.create_job(
            prompt="owned", schedule="every 1h", profile="donna"
        )

        assert jobs.claim_job_for_fire(created["id"], return_job=True) is False

        monkeypatch.setenv("HERMES_HOME", str(donna_home))
        claimed = jobs.claim_job_for_fire(created["id"], return_job=True)
        assert isinstance(claimed, dict)
        assert claimed["owner_profile"] == "donna"
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_resolve_profile_home_maps_names(tmp_path, monkeypatch):
    """resolve_profile_home maps default/named profiles to homes and returns
    None for a missing profile."""
    root, donna_home = _profile_env(tmp_path, monkeypatch, active="default")
    import cron.jobs as jobs
    importlib.reload(jobs)
    try:
        victor_home = root / "profiles" / "victor"
        victor_home.mkdir(parents=True)
        assert jobs.resolve_profile_home("default").resolve() == root.resolve()
        assert jobs.resolve_profile_home("").resolve() == root.resolve()
        assert jobs.resolve_profile_home("donna").resolve() == donna_home.resolve()
        assert jobs._normalize_owner_profile("victor") == "victor"
        resolved_victor = jobs.resolve_profile_home("victor")
        assert resolved_victor is not None
        assert resolved_victor.resolve() == victor_home.resolve()
        assert jobs.resolve_profile_home("ghost") is None
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_normalize_backfills_legacy_profile_to_default(tmp_path, monkeypatch):
    """A pre-feature job with no profile field reads back as 'default'."""
    import cron.jobs as jobs
    legacy = {"id": "l1", "name": "old", "prompt": "x",
              "schedule": {"kind": "interval", "minutes": 60}}
    assert jobs._normalize_job_record(legacy)["profile"] == "default"


def test_run_job_scopes_execution_to_job_profile(tmp_path, monkeypatch):
    """The decisive test: a ticker running as the ROOT profile executes a
    job tagged profile='donna' with HERMES_HOME pointed at donna's home
    (both the env var and the in-process override), then restores the
    ticker's env afterward."""
    from unittest.mock import MagicMock, patch
    root, donna_home = _profile_env(tmp_path, monkeypatch, active="default")
    (donna_home / "config.yaml").write_text("model:\n  default: openrouter/test\n")

    import hermes_constants
    import cron.jobs as jobs
    import cron.scheduler as sched
    importlib.reload(jobs)
    importlib.reload(sched)

    captured = {}

    def fake_run_conversation(prompt, *a, **k):
        captured["env"] = os.environ.get("HERMES_HOME")
        captured["override"] = hermes_constants.get_hermes_home_override()
        captured["resolved"] = str(hermes_constants.get_hermes_home())
        return {"final_response": "done", "completed": True, "failed": False,
                "turn_exit_reason": "text_response(finish_reason=stop)"}

    job = {"id": "j-donna", "name": "donna-audit", "prompt": "audit",
           "profile": "donna", "schedule": {"kind": "interval", "minutes": 60},
           "deliver": "local", "model": "openrouter/test"}

    before = os.environ.get("HERMES_HOME")
    try:
        fake_agent = MagicMock()
        fake_agent.run_conversation.side_effect = fake_run_conversation
        with patch("cron.scheduler._resolve_origin", return_value=None), \
             patch("dotenv.load_dotenv"), \
             patch("hermes_state.SessionDB", return_value=MagicMock()), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   return_value={"api_key": "k", "base_url": "https://x/v1",
                                 "provider": "openrouter", "api_mode": "chat_completions"}), \
             patch("run_agent.AIAgent", return_value=fake_agent):
            success, output, final, err = sched.run_job(job)

        assert success is True, (success, err)
        # During execution the job ran AS donna:
        assert captured["env"] == str(donna_home)
        assert captured["override"] == str(donna_home)
        assert captured["resolved"] == str(donna_home)
        # After the job, the ticker's HERMES_HOME is restored (no leak):
        assert os.environ.get("HERMES_HOME") == before
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)
        importlib.reload(sched)
