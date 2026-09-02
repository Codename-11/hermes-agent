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


def test_registry_lists_routed_oauth_adapters():
    assert isinstance(get_adapter("auto"), RoutedOAuthAdapter)
    assert isinstance(get_adapter("routed"), RoutedOAuthAdapter)
    assert isinstance(get_adapter("openai-codex"), OpenAICodexAdapter)
    assert "xai-oauth" in ADAPTERS







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
    def __init__(self, base_url: str, bearer: str = "codex-bearer"):
        self._base_url = base_url
        self._bearer = bearer

    def is_authenticated(self):
        return True

    def get_credential(self):
        return UpstreamCredential(bearer=self._bearer, base_url=self._base_url)


def _make_routed_adapter() -> RoutedOAuthAdapter:
    adapter = RoutedOAuthAdapter()
    adapter.xai = FakeAdapter(
        "https://xai.example/v1", bearer="xai-token", retry_bearer="xai-rotated"
    )
    adapter.codex = FakeAdapter("https://codex.example/v1", bearer="codex-token")
    adapter.nous = FakeAdapter(
        "https://nous.example/v1", bearer="nous-token", retry_bearer="nous-refreshed"
    )
    adapter.adapters = [adapter.xai, adapter.codex, adapter.nous]
    return adapter


def test_routed_adapter_routes_by_model_and_delegates_retry():
    adapter = _make_routed_adapter()
    grok_body = json.dumps({"model": "grok-4.3"}).encode()
    grok = adapter.get_credential_for_request("/chat/completions", grok_body)
    _, _, _, context = adapter.prepare_proxy_request(
        "/chat/completions", grok_body, {"Content-Type": "application/json"}
    )
    rotated = adapter.get_retry_credential_for_request(
        context=context, failed_credential=grok, status_code=401
    )

    assert grok.bearer == "xai-token"
    assert rotated is not None and rotated.bearer == "xai-rotated"
    assert adapter.get_credential_for_request(
        "/chat/completions", json.dumps({"model": "gpt-5.5"}).encode()
    ).bearer == "codex-token"
    assert adapter.get_credential_for_request(
        "/chat/completions", json.dumps({"model": "anthropic/claude-sonnet-4.6"}).encode()
    ).bearer == "nous-token"


def test_routed_adapter_inventory_is_auth_gated_and_tier_aware(monkeypatch):
    monkeypatch.delenv("HERMES_PROXY_MODEL_ADVERTISE_MODE", raising=False)
    adapter = _make_routed_adapter()
    monkeypatch.setattr(
        "hermes_cli.proxy.adapters.routed.provider_model_ids",
        lambda provider: {
            "xai-oauth": ["grok-4.3", "grok-2", "grok-imagine-video"],
            "openai-codex": ["gpt-5.5", "gpt-5.4", "gpt-image-2"],
            "nous": ["anthropic/claude-sonnet-4.6"],
        }[provider],
    )

    rows = adapter.available_models
    assert {row["id"] for row in rows} == {
        "grok-4.3", "gpt-5.5", "gpt-5.4", "anthropic/claude-sonnet-4.6"
    }
    assert all(row["hermes_capabilities"] == ["chat"] for row in rows)


def test_routed_adapter_routable_inventory_honors_fresh_health_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROXY_MODEL_ADVERTISE_MODE", "routable")
    monkeypatch.setenv("HERMES_PROXY_MODEL_HEALTH_CACHE", str(tmp_path / "health.json"))
    monkeypatch.setenv("HERMES_PROXY_MODEL_HEALTH_TTL_SECONDS", "3600")
    monkeypatch.setattr(
        "hermes_cli.proxy.adapters.routed.provider_model_ids",
        lambda provider: {
            "xai-oauth": ["grok-4.3"],
            "openai-codex": ["gpt-5.5", "gpt-5.4"],
            "nous": [],
        }[provider],
    )
    (tmp_path / "health.json").write_text(json.dumps({"models": {
        "grok-4.3": {"status": "up", "checked_at": time.time()},
        "gpt-5.5": {"status": "up", "checked_at": 1},
        "gpt-5.4": {"status": "healthy", "checked_at": time.time()},
    }}))

    rows = _make_routed_adapter().available_models
    assert {row["id"] for row in rows} == {"grok-4.3", "gpt-5.4"}
    assert all(row["hermes_health"] == "up" for row in rows)


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


def _build_fake_codex_upstream(captured: Dict[str, Any]) -> "web.Application":
    async def responses(request):
        captured["requests"].append({
            "path": request.path,
            "auth": request.headers.get("Authorization"),
            "body": json.loads((await request.read()).decode()),
        })
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(
            b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
        )
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/responses", responses)
    return app


def test_codex_adapter_translates_chat_completions_to_responses():
    rel_path, body, headers, context = OpenAICodexAdapter().prepare_proxy_request(
        "/chat/completions",
        json.dumps({
            "model": "gpt-5.5",
            "messages": [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "Reply exactly: ok"},
            ],
            "stream": False,
        }).encode(),
        {"Content-Type": "application/json"},
    )
    payload = json.loads(body)
    assert rel_path == "/responses"
    assert headers["Content-Type"] == "application/json"
    assert context["codex_chat_completion"] is True
    assert payload["instructions"] == "Be terse."
    assert payload["stream"] is True and payload["store"] is False


def test_server_translates_codex_nonstream_response_and_synthesizes_models():
    async def run():
        captured: Dict[str, Any] = {"requests": []}
        upstream_runner, upstream_base = await _start_runner(_build_fake_codex_upstream(captured))
        adapter = FakeCodexAdapter(f"{upstream_base}/v1", bearer="real-codex-token")
        adapter.available_models = [{"id": "gpt-5.5", "object": "model"}]
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{proxy_base}/v1/models") as resp:
                    assert [row["id"] for row in (await resp.json())["data"]] == ["gpt-5.5"]
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={"model": "gpt-5.5", "messages": [{"role": "user", "content": "ok"}]},
                ) as resp:
                    result = await resp.json()
                    assert result["choices"][0]["message"]["content"] == "ok"
            assert captured["requests"][0]["path"] == "/v1/responses"
            assert captured["requests"][0]["auth"] == "Bearer real-codex-token"
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_server_routed_retry_preserves_selected_provider():
    async def run():
        captured: Dict[str, Any] = {"requests": []}
        upstream_runner, upstream_base = await _start_runner(_build_retrying_fake_upstream(captured))
        adapter = RoutedOAuthAdapter()
        adapter.xai = FakeAdapter(
            f"{upstream_base}/v1", bearer="jwt-bearer", retry_bearer="legacy-bearer"
        )
        adapter.codex = FakeAdapter("https://unused.example/v1")
        adapter.nous = FakeAdapter("https://unused.example/v1")
        adapter.adapters = [adapter.xai, adapter.codex, adapter.nous]
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions", json={"model": "grok-4.3"}
                ) as resp:
                    assert resp.status == 200
            assert [row["auth"] for row in captured["requests"]] == [
                "Bearer jwt-bearer", "Bearer legacy-bearer"
            ]
            assert adapter.xai.retry_calls == 1
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


def _build_sse_upstream(
    frames: list[bytes],
    *,
    path: str = "/v1/chat/completions",
) -> "web.Application":
    async def sse(request):
        _ = await request.read()
        resp = web.StreamResponse(
            status=200, headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)
        for chunk in frames:
            await resp.write(chunk)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_route("*", path, sse)
    return app


def test_proxy_appends_done_when_upstream_omits_sentinel():
    """#90848: complete Portal-shaped SSE without [DONE] gets one appended."""
    async def run():
        frames = [
            b'data: {"choices":[{"delta":{"content":"LONGCAT_OK"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b'data: {"choices":[],"lastOne":true,"usage":{"prompt_tokens":1}}\n\n',
        ]
        upstream_runner, upstream_base = await _start_runner(
            _build_sse_upstream(frames)
        )
        adapter = FakeAdapter(f"{upstream_base}/v1", bearer="ours")
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={"stream": True},
                ) as resp:
                    body = await resp.read()
            text = body.decode("utf-8")
            assert 'data: {"choices":[{"delta":{"content":"LONGCAT_OK"}}]}' in text
            assert '"finish_reason":"stop"' in text
            assert '"lastOne":true' in text
            assert text.count("data: [DONE]") == 1
            assert text.rstrip().endswith("data: [DONE]")
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_proxy_does_not_duplicate_existing_done():
    async def run():
        frames = [
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        upstream_runner, upstream_base = await _start_runner(
            _build_sse_upstream(frames)
        )
        adapter = FakeAdapter(f"{upstream_base}/v1", bearer="ours")
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={"stream": True},
                ) as resp:
                    body = await resp.read()
            assert body.decode("utf-8").count("data: [DONE]") == 1
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_proxy_does_not_append_done_after_error_event():
    async def run():
        frames = [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
            b'data: {"error":{"message":"boom","type":"api_error"}}\n\n',
        ]
        upstream_runner, upstream_base = await _start_runner(
            _build_sse_upstream(frames)
        )
        adapter = FakeAdapter(f"{upstream_base}/v1", bearer="ours")
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={"stream": True},
                ) as resp:
                    body = await resp.read()
            assert "data: [DONE]" not in body.decode("utf-8")
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


def test_proxy_does_not_append_done_after_malformed_trailing_frame():
    async def run():
        frames = [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b'data: {"choices": [MALFORMED]}\n\n',
        ]
        upstream_runner, upstream_base = await _start_runner(
            _build_sse_upstream(frames)
        )
        adapter = FakeAdapter(f"{upstream_base}/v1", bearer="ours")
        proxy_runner, proxy_base = await _start_runner(create_app(adapter))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_base}/v1/chat/completions",
                    json={"stream": True},
                ) as resp:
                    body = await resp.read()
            assert "data: [DONE]" not in body.decode("utf-8")
        finally:
            await proxy_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------






