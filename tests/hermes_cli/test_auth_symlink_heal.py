"""Shared auth stores must survive OAuth fork healing (upstream #101356)."""

import json
import os
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def stores(tmp_path, monkeypatch):
    import hermes_constants
    from hermes_cli import auth

    root = tmp_path / "hermes-root"
    profile = root / "profiles" / "named"
    profile.mkdir(parents=True)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home))
    monkeypatch.setenv("HERMES_HOME", str(profile))
    for name in ("ANTHROPIC_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)
    monkeypatch.setattr(auth, "_global_auth_store_cache", None)
    monkeypatch.setattr(auth, "_oauth_heal_clean_marks", {})
    monkeypatch.setattr(auth, "_oauth_heal_notices", [])
    return root / "auth.json", profile / "auth.json"


def _store(provider, *, provider_block=False):
    row = {
        "id": "test-grant", "label": "named-grant", "auth_type": "oauth",
        "priority": 0, "source": "manual:device_code",
        "access_token": "test-access", "refresh_token": "test-refresh",
        "expires_at": time.time() + 86400,
        "expires_at_ms": int((time.time() + 86400) * 1000),
    }
    return {
        "version": 1,
        "providers": {provider: {"tokens": {
            "access_token": "test-access", "refresh_token": "test-refresh",
        }}} if provider_block else {},
        "credential_pool": {provider: [row]},
    }


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex", "xai-oauth"])
@pytest.mark.parametrize("relative", [False, True])
def test_heal_shared_store_is_byte_for_byte_noop(stores, provider, relative):
    from hermes_cli.auth import heal_forked_single_use_oauth_grants

    root, profile = stores
    root.write_text(json.dumps(_store(provider, provider_block=True)))
    profile.symlink_to(os.path.relpath(root, profile.parent) if relative else root)
    before = root.read_bytes()
    stamp = root.stat().st_mtime_ns
    for _ in range(2):
        assert heal_forked_single_use_oauth_grants(provider) is None
        assert root.read_bytes() == before
        assert root.stat().st_mtime_ns == stamp
        assert profile.is_symlink()


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex", "xai-oauth"])
def test_load_pool_preserves_shared_grant_across_repeated_loads(stores, provider):
    from agent.credential_pool import load_pool

    root, profile = stores
    root.write_text(json.dumps(_store(provider)))
    profile.symlink_to(root)
    for _ in range(2):
        entries = load_pool(provider).entries()
        assert len(entries) == 1
        assert entries[0].id == "test-grant"
        assert entries[0].access_token == "test-access"
        saved = json.loads(root.read_text())["credential_pool"][provider]
        assert len(saved) == 1
        assert saved[0]["refresh_token"] == "test-refresh"
        assert profile.is_symlink()


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex", "xai-oauth"])
def test_distinct_copied_store_still_heals(stores, provider):
    from hermes_cli.auth import heal_forked_single_use_oauth_grants

    root, profile = stores
    payload = json.dumps(_store(provider))
    root.write_text(payload)
    profile.write_text(payload)
    result = heal_forked_single_use_oauth_grants(provider)
    assert result is not None
    assert result["stripped_ids"] == ["test-grant"]
    assert not json.loads(profile.read_text())["credential_pool"].get(provider)
    assert json.loads(root.read_text())["credential_pool"][provider][0]["refresh_token"] == "test-refresh"


def _fake_login(monkeypatch, provider, serial):
    from hermes_cli import auth

    tokens = {"access_token": f"test-access-{serial}", "refresh_token": f"test-refresh-{serial}"}
    if provider == "anthropic":
        from agent import anthropic_adapter
        monkeypatch.setattr(anthropic_adapter, "run_hermes_oauth_login_pure", lambda: {
            **tokens, "expires_at_ms": int((time.time() + 86400) * 1000),
        })
    else:
        method = "_codex_device_code_login" if provider == "openai-codex" else "_xai_oauth_device_code_login"
        monkeypatch.setattr(auth, method, lambda **kwargs: {"tokens": tokens})


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex", "xai-oauth"])
@pytest.mark.parametrize("layout", ["root", "profile", "symlink"])
def test_auth_add_first_and_second_login_survive_fresh_load(stores, monkeypatch, capsys, provider, layout):
    from agent.credential_pool import load_pool
    from hermes_cli import auth, auth_commands

    root, profile = stores
    root.write_text(json.dumps({"providers": {}, "credential_pool": {}}))
    if layout == "root":
        monkeypatch.setenv("HERMES_HOME", str(root.parent))
        active = root
    else:
        active = profile
        if layout == "symlink":
            profile.symlink_to(root)
    for serial in (1, 2):
        _fake_login(monkeypatch, provider, serial)
        auth_commands.auth_add_command(SimpleNamespace(
            provider=provider, auth_type="oauth", label=f"account-{serial}",
        ))
        assert f"credential #{serial}" in capsys.readouterr().out
        saved = json.loads(active.read_text())["credential_pool"][provider]
        assert len(saved) == serial
        auth._global_auth_store_cache = None
        auth._oauth_heal_clean_marks.clear()
        entries = load_pool(provider).entries()
        assert {e.refresh_token for e in entries} == {f"test-refresh-{n}" for n in range(1, serial + 1)}
    if layout == "profile":
        assert not json.loads(root.read_text())["credential_pool"].get(provider)
    if layout == "symlink":
        assert profile.is_symlink()


def test_auth_add_write_failure_does_not_claim_success(stores, monkeypatch, capsys):
    from hermes_cli import auth, auth_commands

    root, profile = stores
    root.write_text(json.dumps({"providers": {}, "credential_pool": {}}))
    profile.symlink_to(root)
    _fake_login(monkeypatch, "openai-codex", 1)

    def fail_write(store, *args, **kwargs):
        assert store["credential_pool"]["openai-codex"][0]["refresh_token"] == "test-refresh-1"
        raise OSError("simulated disk failure")

    monkeypatch.setattr(auth, "_save_auth_store", fail_write)
    with pytest.raises(OSError, match="simulated disk failure"):
        auth_commands.auth_add_command(SimpleNamespace(
            provider="openai-codex", auth_type="oauth", label="account-1",
        ))
    assert "Added" not in capsys.readouterr().out
    assert not json.loads(root.read_text())["credential_pool"].get("openai-codex")
