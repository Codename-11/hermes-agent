"""Tests for the `hermes proxy` subcommand and its upstream adapters."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.proxy.adapters import ADAPTERS, get_adapter
from hermes_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential
from hermes_cli.proxy.adapters.nous_portal import NousPortalAdapter
from hermes_cli.proxy.adapters.openai_codex import OpenAICodexAdapter
from hermes_cli.proxy.adapters.routed import RoutedOAuthAdapter
from hermes_cli.proxy.adapters.xai import XAIGrokAdapter


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


def test_registry_lists_nous():
    assert "nous" in ADAPTERS


def test_registry_lists_routed_auto_adapter():
    assert "auto" in ADAPTERS
    assert "routed" in ADAPTERS
    assert isinstance(get_adapter("auto"), RoutedOAuthAdapter)
    assert isinstance(get_adapter("routed"), RoutedOAuthAdapter)


def test_get_adapter_returns_instance():
    adapter = get_adapter("nous")
    assert isinstance(adapter, NousPortalAdapter)
    assert isinstance(adapter, UpstreamAdapter)


def test_get_adapter_case_insensitive():
    assert isinstance(get_adapter("NOUS"), NousPortalAdapter)
    assert isinstance(get_adapter("  Nous  "), NousPortalAdapter)


def test_get_adapter_unknown_provider_raises():
    with pytest.raises(ValueError, match="anthropic"):
        get_adapter("anthropic")  # not yet implemented


# ---------------------------------------------------------------------------
# NousPortalAdapter
# ---------------------------------------------------------------------------


def _write_auth_store(hermes_home: Path, nous_state: Dict[str, Any]) -> Path:
    """Write an auth.json with the given nous state into a hermetic HERMES_HOME."""
    auth_path = hermes_home / "auth.json"
    auth_path.write_text(json.dumps({
        "version": 1,
        "providers": {"nous": nous_state},
    }))
    return auth_path




def test_nous_adapter_concurrent_refresh_serialized(tmp_path, monkeypatch):
    """Two parallel get_credential() calls must serialize through the lock."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_auth_store(tmp_path, {
        "access_token": "a", "refresh_token": "r",
    })

    call_log: list = []
    in_flight = threading.Event()
    overlap_detected = threading.Event()
    counter = [0]
    counter_lock = threading.Lock()

    def serializing_refresh(**kwargs):
        # If another thread is already inside refresh, the lock is broken.
        if in_flight.is_set():
            overlap_detected.set()
        in_flight.set()
        try:
            call_log.append(threading.current_thread().ident)
            # Simulate refresh latency so any race window is exposed.
            import time
            time.sleep(0.05)
            with counter_lock:
                counter[0] += 1
                idx = counter[0]
            return {
                "api_key": f"key-{idx}",
                "expires_at": "2099-01-01T00:00:00Z",
                "base_url": "https://inference-api.nousresearch.com/v1",
            }
        finally:
            in_flight.clear()

    adapter = NousPortalAdapter()
    results: list = []
    errors: list = []

    def worker():
        try:
            results.append(adapter.get_credential().bearer)
        except Exception as exc:  # pragma: no cover - shouldn't happen
            errors.append(exc)

    with patch(
        "hermes_cli.proxy.adapters.nous_portal.resolve_nous_runtime_credentials",
        side_effect=serializing_refresh,
    ):
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not errors, f"workers errored: {errors}"
    assert len(results) == 3
    assert len(call_log) == 3
    assert not overlap_detected.is_set(), "refresh calls overlapped — lock is broken"
    assert all(r.startswith("key-") for r in results)


# ---------------------------------------------------------------------------
# XAIGrokAdapter
# ---------------------------------------------------------------------------


def _write_xai_pool_entry(
    hermes_home: Path,
    *,
    access_token: str = "xai-access-token",
    refresh_token: str = "xai-refresh-token",
    base_url: str = "https://api.x.ai/v1",
    source: str = "manual:xai_pkce",
) -> Path:
    """Write an xai-oauth pool entry into a hermetic HERMES_HOME."""
    auth_path = hermes_home / "auth.json"
    auth_path.write_text(json.dumps({
        "version": 1,
        "providers": {},
        "credential_pool": {
            "xai-oauth": [
                {
                    "id": "xai123",
                    "label": "xai-test",
                    "auth_type": "oauth",
                    "priority": 0,
                    "source": source,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "base_url": base_url,
                }
            ]
        },
    }))
    return auth_path


def test_xai_adapter_not_authenticated_when_no_pool_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(json.dumps({
        "version": 1,
        "providers": {},
        "credential_pool": {},
    }))
    assert not XAIGrokAdapter().is_authenticated()


def test_xai_adapter_retry_rotates_pool_entry_on_429(tmp_path, monkeypatch):
    """429 from xAI must rotate to the next pool entry, not attempt refresh.

    Pre-fix (#28932) ``get_retry_credential`` only fired on 401, so a 429
    rate-limit response flowed back to the client unchanged AND the
    rate-limited bearer stayed active for the next request — defeating
    the whole point of pool rotation.

    Post-fix: 429 lands on ``mark_exhausted_and_rotate`` (no refresh —
    that's irrelevant for rate limits), stamps the 1-hour cooldown
    via ``EXHAUSTED_TTL_429_SECONDS`` on the offending key, and
    returns the next available credential.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Two pool entries so rotation has somewhere to go.
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({
        "version": 1,
        "providers": {},
        "credential_pool": {
            "xai-oauth": [
                {
                    "id": "xai-first",
                    "label": "xai-first",
                    "auth_type": "oauth",
                    "priority": 0,
                    "source": "manual:xai_pkce",
                    "access_token": "first-access-token",
                    "refresh_token": "first-refresh-token",
                    "base_url": "https://api.x.ai/v1",
                },
                {
                    "id": "xai-second",
                    "label": "xai-second",
                    "auth_type": "oauth",
                    "priority": 1,
                    "source": "manual:xai_pkce",
                    "access_token": "second-access-token",
                    "refresh_token": "second-refresh-token",
                    "base_url": "https://api.x.ai/v1",
                },
            ]
        },
    }))

    # Refresh must NOT be called on the 429 path — guard against
    # the fix accidentally trying to refresh-on-rate-limit.
    def _refresh_must_not_run(*args, **kwargs):
        raise AssertionError("refresh_xai_oauth_pure must not run on 429")

    monkeypatch.setattr("hermes_cli.auth.refresh_xai_oauth_pure", _refresh_must_not_run)

    adapter = XAIGrokAdapter()
    failed = adapter.get_credential()
    assert failed.bearer == "first-access-token", "starting bearer should be the first entry"

    retry = adapter.get_retry_credential(
        failed_credential=failed,
        status_code=429,
    )

    assert retry is not None, "429 must rotate to next pool entry"
    assert retry.bearer == "second-access-token", (
        f"expected rotation to second entry, got {retry.bearer!r}"
    )


# ---------------------------------------------------------------------------
# Server: path filtering + forwarding
#
# We run the proxy AND a fake upstream as real aiohttp servers on ephemeral
# ports. Avoids pytest-aiohttp's fixtures (extra dependency for one test file).
# ---------------------------------------------------------------------------

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from hermes_cli.proxy.server import create_app  # noqa: E402


class FakeAdapter(UpstreamAdapter):
    """A test adapter that returns a fixed credential without touching disk."""

    def __init__(self, base_url: str, bearer: str = "test-bearer",
                 allowed=None, raise_on_credential=False,
                 retry_bearer: str | None = None):
        self._base_url = base_url
        self._bearer = bearer
        self._allowed = frozenset(allowed or ["/chat/completions"])
        self._raise = raise_on_credential
        self._retry_bearer = retry_bearer
        self.calls = 0
        self.retry_calls = 0

    @property
    def name(self): return "fake"

    @property
    def display_name(self): return "Fake Provider"

    @property
    def allowed_paths(self): return self._allowed

    def is_authenticated(self): return True

    def get_credential(self):
        self.calls += 1
        if self._raise:
            raise RuntimeError("simulated auth failure")
        return UpstreamCredential(
            bearer=self._bearer, base_url=self._base_url,
            expires_at="2099-01-01T00:00:00Z",
        )

    def get_retry_credential(self, *, failed_credential, status_code):
        _ = failed_credential
        self.retry_calls += 1
        if status_code != 401 or not self._retry_bearer:
            return None
        return UpstreamCredential(
            bearer=self._retry_bearer,
            base_url=self._base_url,
            expires_at="2099-01-01T00:00:00Z",
        )


class FakeCodexAdapter(OpenAICodexAdapter):
    """Codex adapter wired to a local fake upstream for transform tests."""

    def __init__(self, base_url: str, bearer: str = "codex-bearer"):
        self._base_url = base_url
        self._bearer = bearer

    def is_authenticated(self):
        return True

    def get_credential(self):
        return UpstreamCredential(
            bearer=self._bearer,
            base_url=self._base_url,
            expires_at="2099-01-01T00:00:00Z",
        )


# ---------------------------------------------------------------------------
# Routed adapter
# ---------------------------------------------------------------------------


def test_routed_adapter_routes_credentials_by_requested_model():
    adapter = RoutedOAuthAdapter()
    setattr(adapter, "xai", FakeAdapter("https://xai.example/v1", bearer="xai-token"))
    setattr(adapter, "codex", FakeAdapter("https://codex.example/v1", bearer="codex-token"))
    setattr(adapter, "nous", FakeAdapter("https://nous.example/v1", bearer="nous-token"))
    adapter.adapters = [adapter.xai, adapter.codex, adapter.nous]

    grok = adapter.get_credential_for_request(
        "/chat/completions",
        json.dumps({"model": "grok-4.3"}).encode(),
    )
    codex = adapter.get_credential_for_request(
        "/chat/completions",
        json.dumps({"model": "gpt-5.4"}).encode(),
    )
    nous = adapter.get_credential_for_request(
        "/chat/completions",
        json.dumps({"model": "hermes-4-405b"}).encode(),
    )
    slash_prefixed = adapter.get_credential_for_request(
        "/chat/completions",
        json.dumps({"model": "anthropic/claude-sonnet-4.6"}).encode(),
    )

    assert grok.bearer == "xai-token"
    assert grok.base_url == "https://xai.example/v1"
    assert codex.bearer == "codex-token"
    assert codex.base_url == "https://codex.example/v1"
    assert nous.bearer == "nous-token"
    assert nous.base_url == "https://nous.example/v1"
    assert slash_prefixed.bearer == "nous-token"
    assert slash_prefixed.base_url == "https://nous.example/v1"


def _make_authenticated_routed_adapter() -> RoutedOAuthAdapter:
    adapter = RoutedOAuthAdapter()
    setattr(adapter, "xai", FakeAdapter("https://xai.example/v1", bearer="xai-token"))
    setattr(adapter, "codex", FakeAdapter("https://codex.example/v1", bearer="codex-token"))
    setattr(adapter, "nous", FakeAdapter("https://nous.example/v1", bearer="nous-token"))
    adapter.adapters = [adapter.xai, adapter.codex, adapter.nous]
    return adapter


def test_routed_adapter_default_inventory_requires_authenticated_adapters(monkeypatch):
    monkeypatch.delenv("HERMES_PROXY_MODEL_ADVERTISE_MODE", raising=False)
    adapter = RoutedOAuthAdapter()
    # A temp HERMES_HOME has no auth store, so the default inventory should not
    # fall back to optimistic static ghosts.
    assert adapter.available_models == []


def test_routed_adapter_legacy_all_mode_keeps_static_fallback(monkeypatch):
    monkeypatch.setenv("HERMES_PROXY_MODEL_ADVERTISE_MODE", "all")
    adapter = RoutedOAuthAdapter()
    model_ids = {item["id"] for item in adapter.available_models}
    assert {"grok-4.3", "gpt-5.5", "gpt-5.4"}.issubset(model_ids)


def test_routed_adapter_advertises_authenticated_chat_model_list(monkeypatch):
    monkeypatch.delenv("HERMES_PROXY_MODEL_ADVERTISE_MODE", raising=False)

    def fake_provider_model_ids(provider):
        return {
            "xai-oauth": [
                "grok-4.3",
                "grok-4.20-0309-reasoning",
                "grok-4.20-multi-agent-0309",
                "grok-2",
                "grok-beta",
                "grok-imagine-video",
            ],
            "openai-codex": ["gpt-5.5", "gpt-5.4", "gpt-image-2"],
            "nous": [],
        }[provider]

    monkeypatch.setattr(
        "hermes_cli.proxy.adapters.routed.provider_model_ids",
        fake_provider_model_ids,
    )
    adapter = _make_authenticated_routed_adapter()

    rows = adapter.available_models
    model_ids = {item["id"] for item in rows}

    assert {"grok-4.3", "grok-4.20-0309-reasoning", "gpt-5.5", "gpt-5.4"}.issubset(model_ids)
    assert "grok-4.20-multi-agent-0309" not in model_ids
    assert "grok-2" not in model_ids
    assert "grok-beta" not in model_ids
    assert "grok-imagine-video" not in model_ids
    assert "gpt-image-2" not in model_ids
    assert all(item["hermes_capabilities"] == ["chat"] for item in rows)
    assert all(item["hermes_health"] == "unknown" for item in rows)


def test_routed_adapter_routable_mode_uses_health_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROXY_MODEL_ADVERTISE_MODE", "routable")
    monkeypatch.setenv("HERMES_PROXY_MODEL_HEALTH_CACHE", str(tmp_path / "health.json"))
    monkeypatch.setenv("HERMES_PROXY_MODEL_HEALTH_TTL_SECONDS", "3600")

    def fake_provider_model_ids(provider):
        return {
            "xai-oauth": ["grok-4.3", "grok-4.20-0309-reasoning"],
            "openai-codex": ["gpt-5.5", "gpt-5.4"],
            "nous": [],
        }[provider]

    monkeypatch.setattr(
        "hermes_cli.proxy.adapters.routed.provider_model_ids",
        fake_provider_model_ids,
    )
    (tmp_path / "health.json").write_text(json.dumps({
        "models": {
            "grok-4.3": {"status": "down", "checked_at": time.time()},
            "grok-4.20-0309-reasoning": {"status": "up", "checked_at": time.time()},
            "gpt-5.5": {"status": "up", "checked_at": 1},
            "gpt-5.4": {"status": "healthy", "checked_at": time.time()},
        }
    }))

    adapter = _make_authenticated_routed_adapter()
    rows = adapter.available_models
    model_ids = {item["id"] for item in rows}

    assert model_ids == {"grok-4.20-0309-reasoning", "gpt-5.4"}
    assert all(item["hermes_health"] == "up" for item in rows)


async def _start_runner(app: "web.Application"):
    """Spin up an aiohttp app on an ephemeral localhost port. Returns (runner, base_url)."""
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    sockets = list(site._server.sockets)  # type: ignore[union-attr]
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def _build_fake_upstream(captured: Dict[str, Any]) -> "web.Application":
    async def echo(request):
        body = await request.read()
        captured["requests"].append({
            "method": request.method,
            "path": request.path,
            "auth": request.headers.get("Authorization"),
            "body": body.decode("utf-8") if body else "",
        })
        return web.json_response({"echoed": True, "path": request.path})

    async def sse(request):
        resp = web.StreamResponse(
            status=200, headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)
        for chunk in [b"data: hello\n\n", b"data: world\n\n", b"data: [DONE]\n\n"]:
            await resp.write(chunk)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_route("*", "/v1/chat/completions", echo)
    app.router.add_route("*", "/v1/embeddings", echo)
    app.router.add_route("*", "/v1/sse", sse)
    return app


def _build_retrying_fake_upstream(captured: Dict[str, Any]) -> "web.Application":
    async def maybe_unauthorized(request):
        body = await request.read()
        auth = request.headers.get("Authorization")
        captured["requests"].append({
            "method": request.method,
            "path": request.path,
            "auth": auth,
            "body": body.decode("utf-8") if body else "",
        })
        if auth == "Bearer jwt-bearer":
            return web.json_response({"error": "bad token"}, status=401)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_route("*", "/v1/chat/completions", maybe_unauthorized)
    return app


def _build_fake_codex_responses_upstream(captured: Dict[str, Any]) -> "web.Application":
    async def responses(request):
        body = await request.read()
        captured["requests"].append({
            "method": request.method,
            "path": request.path,
            "auth": request.headers.get("Authorization"),
            "body": body.decode("utf-8") if body else "",
        })
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "application/octet-stream"},
        )
        await resp.prepare(request)
        for line in [
            b'event: response.output_text.delta\n',
            b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
            b'event: response.output_text.done\n',
            b'data: {"type":"response.output_text.done","text":"ok"}\n\n',
            b'event: response.completed\n',
            b'data: {"type":"response.completed","response":{"id":"resp_test","model":"gpt-5.5"}}\n\n',
        ]:
            await resp.write(line)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_route("*", "/v1/responses", responses)
    return app


def test_codex_adapter_translates_chat_completion_request_shape():
    adapter = OpenAICodexAdapter()
    rel_path, body, headers, context = adapter.prepare_proxy_request(
        "/chat/completions",
        json.dumps({
            "model": "gpt-5.5",
            "messages": [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "Reply exactly: ok"},
            ],
            "stream": False,
            "temperature": 0.2,
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )

    payload = json.loads(body.decode("utf-8"))
    assert rel_path == "/responses"
    assert headers["Content-Type"] == "application/json"
    assert context["codex_chat_completion"] is True
    assert context["client_stream"] is False
    assert payload["model"] == "gpt-5.5"
    assert payload["instructions"] == "Be terse."
    assert payload["store"] is False
    assert payload["stream"] is True
    assert payload["temperature"] == 0.2
    assert payload["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Reply exactly: ok"}],
        }
    ]


def test_codex_adapter_translates_nonstream_chat_completion_response():
    async def run():
        captured: Dict[str, Any] = {"requests": []}
        upstream_runner, upstream_base = await _start_runner(
            _build_fake_codex_responses_upstream(captured)
        )
        adapter = FakeCodexAdapter(f"{upstream_base}/v1", bearer="real-codex-key")
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={
                        "model": "gpt-5.5",
                        "messages": [{"role": "user", "content": "Reply exactly: ok"}],
                        "stream": False,
                    },
                    headers={"Authorization": "Bearer client-dummy-key"},
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["object"] == "chat.completion"
                    assert data["model"] == "gpt-5.5"
                    assert data["choices"][0]["message"] == {
                        "role": "assistant",
                        "content": "ok",
                    }
                    assert data["choices"][0]["finish_reason"] == "stop"

            assert len(captured["requests"]) == 1
            req = captured["requests"][0]
            assert req["path"] == "/v1/responses"
            assert req["auth"] == "Bearer real-codex-key"
            upstream_payload = json.loads(req["body"])
            assert upstream_payload["store"] is False
            assert upstream_payload["stream"] is True
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_codex_adapter_translates_stream_chat_completion_response():
    async def run():
        captured: Dict[str, Any] = {"requests": []}
        upstream_runner, upstream_base = await _start_runner(
            _build_fake_codex_responses_upstream(captured)
        )
        adapter = FakeCodexAdapter(f"{upstream_base}/v1", bearer="real-codex-key")
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={
                        "model": "gpt-5.5",
                        "messages": [{"role": "user", "content": "Reply exactly: ok"}],
                        "stream": True,
                    },
                ) as resp:
                    assert resp.status == 200
                    assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
                    body = await resp.text()
                    assert '"object": "chat.completion.chunk"' in body
                    assert '"delta": {"content": "ok"}' in body
                    assert "data: [DONE]" in body
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_server_forwards_chat_completions():
    async def run():
        captured: Dict[str, Any] = {"requests": []}
        upstream_runner, upstream_base = await _start_runner(_build_fake_upstream(captured))
        adapter = FakeAdapter(f"{upstream_base}/v1", bearer="real-portal-key")
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={"model": "Hermes-4-70B",
                          "messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": "Bearer client-dummy-key"},
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["echoed"] is True

            assert len(captured["requests"]) == 1
            req = captured["requests"][0]
            assert req["auth"] == "Bearer real-portal-key"
            assert "Hermes-4-70B" in req["body"]
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_server_retries_once_with_adapter_retry_credential_on_401():
    async def run():
        captured: Dict[str, Any] = {"requests": []}
        upstream_runner, upstream_base = await _start_runner(
            _build_retrying_fake_upstream(captured)
        )
        adapter = FakeAdapter(
            f"{upstream_base}/v1",
            bearer="jwt-bearer",
            retry_bearer="legacy-bearer",
        )
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={"model": "Hermes-4-70B"},
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True

            assert adapter.retry_calls == 1
            assert [req["auth"] for req in captured["requests"]] == [
                "Bearer jwt-bearer",
                "Bearer legacy-bearer",
            ]
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_server_rejects_disallowed_path():
    async def run():
        adapter = FakeAdapter("http://unused.example/v1", allowed=["/chat/completions"])
        runner, base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base}/v1/random/endpoint") as resp:
                    assert resp.status == 404
                    body = await resp.json()
                    assert body["error"]["type"] == "path_not_allowed"
                    assert "/chat/completions" in body["error"]["message"]
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_server_returns_401_when_adapter_fails():
    async def run():
        adapter = FakeAdapter("http://unused.example/v1", raise_on_credential=True)
        runner, base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{base}/v1/chat/completions", json={}) as resp:
                    assert resp.status == 401
                    body = await resp.json()
                    assert body["error"]["type"] == "upstream_auth_failed"
                    assert "simulated auth failure" in body["error"]["message"]
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_server_health_endpoint():
    async def run():
        adapter = FakeAdapter("http://unused.example/v1")
        runner, base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base}/health") as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["status"] == "ok"
                    assert body["upstream"] == "Fake Provider"
                    assert body["authenticated"] is True
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_server_models_endpoint_uses_synthetic_adapter_models():
    async def run():
        adapter = FakeAdapter("http://unused.example/v1")
        setattr(adapter, "available_models", [
            {"id": "grok-4.3", "object": "model", "owned_by": "xai-oauth"},
            {"id": "gpt-5.4", "object": "model", "owned_by": "openai-codex"},
        ])
        runner, base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base}/v1/models") as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert {item["id"] for item in body["data"]} == {"grok-4.3", "gpt-5.4"}
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_server_streams_sse():
    async def run():
        captured: Dict[str, Any] = {"requests": []}
        upstream_runner, upstream_base = await _start_runner(_build_fake_upstream(captured))
        adapter = FakeAdapter(f"{upstream_base}/v1", allowed=["/sse"])
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{proxy_base}/v1/sse") as resp:
                    assert resp.status == 200
                    chunks = []
                    async for chunk in resp.content.iter_any():
                        chunks.append(chunk)
                    full = b"".join(chunks)
                    assert b"data: hello" in full
                    assert b"data: [DONE]" in full
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_server_strips_client_auth_header():
    """The client's Authorization header MUST NOT reach the upstream."""
    async def run():
        captured: Dict[str, Any] = {"requests": []}
        upstream_runner, upstream_base = await _start_runner(_build_fake_upstream(captured))
        adapter = FakeAdapter(f"{upstream_base}/v1", bearer="ours")
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={},
                    headers={"Authorization": "Bearer SHOULD_NOT_LEAK"},
                ) as resp:
                    await resp.read()
            assert captured["requests"][0]["auth"] == "Bearer ours"
            assert "SHOULD_NOT_LEAK" not in captured["requests"][0]["auth"]
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------






