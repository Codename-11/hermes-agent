"""Shared auth stores must survive OAuth fork healing (upstream #101356)."""

import json
import os
import time

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
