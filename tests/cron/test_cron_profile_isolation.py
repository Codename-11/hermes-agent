"""Axiom cron profile isolation contract.

Axiom deliberately keeps cron storage shared at the root Hermes home while
scoping visibility/execution by profile metadata:

- Storage: all profiles write to ``<root>/cron/jobs.json``.
- Visibility: jobs carry ``owner_profile`` / ``scope`` and each active profile
  sees only its own profile-scoped jobs plus global jobs.
- Execution: profile-scoped gateway ticks resolve the active HERMES_HOME at run
  time, so profile jobs still execute with that profile's env/config/scripts.

This differs from upstream's per-profile cron-store direction. The shared store
is intentional on Axiom so cross-profile cron management stays inspectable from
the lead profile without leaking execution context.
"""
import importlib
from pathlib import Path


def _set_profile_env(monkeypatch, root: Path, profile_home: Path) -> None:
    """Pretend the platform default root is ``root`` and the active
    HERMES_HOME is a profile under it (``<root>/profiles/<name>``)."""
    import hermes_constants

    monkeypatch.setattr(
        hermes_constants, "_get_platform_default_hermes_home", lambda: root
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_home))


def test_cron_storage_anchors_at_shared_root_with_profile_owner_metadata(tmp_path, monkeypatch):
    """Under a profile HERMES_HOME, Axiom stores cron rows in the shared
    root store and relies on owner_profile/scope for profile isolation."""
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "coder"
    profile_home.mkdir(parents=True)

    _set_profile_env(monkeypatch, root, profile_home)

    import hermes_constants

    # Sanity: the override is wired the way the gateway sees it.
    assert hermes_constants.get_hermes_home().resolve() == profile_home.resolve()
    assert hermes_constants.get_default_hermes_root().resolve() == root.resolve()

    import cron.jobs as jobs

    importlib.reload(jobs)
    try:
        assert jobs.HERMES_DIR.resolve() == root.resolve()
        assert jobs.JOBS_FILE.resolve() == (root / "cron" / "jobs.json").resolve()
        assert jobs.JOBS_FILE.resolve() != (profile_home / "cron" / "jobs.json").resolve()

        get_active_cron_profile = getattr(jobs, "get_active_cron_profile")
        job_visible = getattr(jobs, "_job_visible_to_active_profile")
        assert get_active_cron_profile() == "coder"
        profile_job = {"owner_profile": "coder", "scope": "profile"}
        other_job = {"owner_profile": "other", "scope": "profile"}
        global_job = {"owner_profile": "other", "scope": "global"}
        assert job_visible(profile_job)
        assert not job_visible(other_job)
        assert job_visible(global_job)
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_cron_lock_path_anchors_at_shared_root(tmp_path, monkeypatch):
    """The tick lock guards the shared store, so it is rooted at
    <root>/cron even when the active HERMES_HOME is a profile."""
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "coder"
    profile_home.mkdir(parents=True)

    _set_profile_env(monkeypatch, root, profile_home)

    import cron.scheduler as scheduler

    lock_dir, lock_file = scheduler._get_lock_paths()
    assert lock_dir.resolve() == (root / "cron").resolve()
    assert lock_file.resolve() == (root / "cron" / ".tick.lock").resolve()
    assert lock_dir.resolve() != (profile_home / "cron").resolve()


def test_cron_execution_home_follows_active_profile(tmp_path, monkeypatch):
    """Execution-time home resolution (.env / config.yaml / scripts) follows
    the active profile, not the shared root — so a profile gateway runs its
    jobs with that profile's runtime config."""
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "coder"
    profile_home.mkdir(parents=True)

    _set_profile_env(monkeypatch, root, profile_home)

    import cron.scheduler as scheduler

    # The module-level test override must be clear so the dynamic path runs.
    monkeypatch.setattr(scheduler, "_hermes_home", None, raising=False)
    assert scheduler._get_hermes_home().resolve() == profile_home.resolve()
    assert scheduler._get_hermes_home().resolve() != root.resolve()


def test_cron_storage_unaffected_when_no_profile(tmp_path, monkeypatch):
    """With no profile (HERMES_HOME == root), the store is the root's cron dir
    — unchanged behavior for single-profile installs."""
    root = tmp_path / "hermes_home"
    root.mkdir(parents=True)

    import hermes_constants

    monkeypatch.setattr(
        hermes_constants, "_get_platform_default_hermes_home", lambda: root
    )
    monkeypatch.setenv("HERMES_HOME", str(root))

    import cron.jobs as jobs

    importlib.reload(jobs)
    try:
        assert jobs.HERMES_DIR.resolve() == root.resolve()
        assert jobs.JOBS_FILE.resolve() == (root / "cron" / "jobs.json").resolve()
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)
