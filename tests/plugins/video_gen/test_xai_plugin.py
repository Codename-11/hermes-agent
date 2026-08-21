"""Smoke tests for the xAI video gen plugin — load & register surface."""

from __future__ import annotations

import pytest

from agent import video_gen_registry


@pytest.fixture(autouse=True)
def _reset_registry():
    video_gen_registry._reset_for_tests()
    yield
    video_gen_registry._reset_for_tests()


def test_xai_provider_registers():
    from plugins.video_gen.xai import XAIVideoGenProvider

    provider = XAIVideoGenProvider()
    video_gen_registry.register_provider(provider)

    assert video_gen_registry.get_provider("xai") is provider
    assert provider.display_name == "xAI"
    assert provider.default_model() == "grok-imagine-video"


def test_xai_provider_lists_text_and_current_image_video_models():
    from plugins.video_gen.xai import XAIVideoGenProvider

    models = XAIVideoGenProvider().list_models()
    ids = [model["id"] for model in models]

    assert ids[0] == "grok-imagine-video"
    assert ids[1] == "grok-imagine-video-1.5"
    assert models[1]["modalities"] == ["image"]
    assert "aliases" not in models[1]


def test_xai_routes_default_models_by_modality():
    from plugins.video_gen.xai import _resolve_model_for_modality

    assert _resolve_model_for_modality(
        "grok-imagine-video",
        modality="text",
        explicit_model=False,
    ) == "grok-imagine-video"
    assert _resolve_model_for_modality(
        "grok-imagine-video",
        modality="image",
        explicit_model=False,
    ) == "grok-imagine-video-1.5"
    assert _resolve_model_for_modality(
        "grok-imagine-video-1.5-preview",
        modality="text",
        explicit_model=False,
    ) == "grok-imagine-video"
    assert _resolve_model_for_modality(
        "grok-imagine-video-1.5-preview",
        modality="text",
        explicit_model=True,
    ) == "grok-imagine-video-1.5-preview"


def test_xai_capabilities_keep_generate_surface_only():
    from plugins.video_gen.xai import XAIVideoGenProvider

    caps = XAIVideoGenProvider().capabilities()
    assert caps["modalities"] == ["text", "image"]
    assert "operations" not in caps
    assert caps["max_reference_images"] == 7
    assert "1080p" in caps["resolutions"]


def test_xai_extension_rejects_custom_1080p_instead_of_silently_downscaling(
    monkeypatch,
):
    import json
    import tools.xai_video_tools as xai_video_tools

    called = False

    def _unexpected_extend(**_kwargs):
        nonlocal called
        called = True
        return {"success": True}

    monkeypatch.setattr(xai_video_tools, "run_xai_video_extend", _unexpected_extend)
    result = json.loads(
        xai_video_tools._handle_xai_video_extend(
            {
                "prompt": "continue",
                "video_url": "https://example.com/source.mp4",
                "resolution": "1080p",
            }
        )
    )

    assert result.get("success") is not True
    assert "720p" in result["error"]
    assert called is False


def test_xai_unavailable_without_key(monkeypatch):
    from plugins.video_gen.xai import XAIVideoGenProvider

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert XAIVideoGenProvider().is_available() is False


def test_xai_generate_requires_xai_key(monkeypatch):
    from plugins.video_gen.xai import XAIVideoGenProvider

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    result = XAIVideoGenProvider().generate("a happy dog")
    assert result["success"] is False
    assert result["error_type"] == "auth_required"


def test_xai_available_with_oauth_only(monkeypatch):
    """The plugin must honour xAI Grok OAuth credentials, not just
    XAI_API_KEY. Otherwise the agent's tool-availability check filters
    ``video_generate`` out of the toolbelt and the agent silently falls
    back to whatever skill advertises video generation (e.g. comfyui).
    """
    import plugins.video_gen.xai as xai_plugin

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "tools.xai_http.resolve_xai_http_credentials",
        lambda: {
            "provider": "xai-oauth",
            "api_key": "oauth-bearer-token",
            "base_url": "https://api.x.ai/v1",
        },
    )

    assert xai_plugin.XAIVideoGenProvider().is_available() is True


def test_xai_resolved_credentials_threaded_through_request(monkeypatch):
    """OAuth-resolved creds must reach the HTTP layer — bug class where
    ``is_available()`` says yes but the request still hits with no key.
    """
    import plugins.video_gen.xai as xai_plugin

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "tools.xai_http.resolve_xai_http_credentials",
        lambda: {
            "provider": "xai-oauth",
            "api_key": "oauth-bearer-token",
            "base_url": "https://api.x.ai/v1",
        },
    )

    api_key, base_url = xai_plugin._resolve_xai_credentials()
    assert api_key == "oauth-bearer-token"
    assert base_url == "https://api.x.ai/v1"
    headers = xai_plugin._xai_headers(api_key)
    assert headers["Authorization"] == "Bearer oauth-bearer-token"


@pytest.mark.asyncio
async def test_video_input_from_public_url_uses_url_field():
    from plugins.video_gen.xai import _video_input_from_public_url

    url = "https://files-cdn.x.ai/kRQVP6PRQlioVAUNC3GAdg/file_1faca9c3-9411-46ad-bb41-b9b8527789e6.mp4"
    result = await _video_input_from_public_url(
        url,
        api_key="test-key",
        base_url="https://api.x.ai/v1",
    )
    assert result == {"url": url}


def test_xai_video_image_input_blocks_credential_store_symlink(tmp_path, monkeypatch):
    from plugins.video_gen.xai import _image_ref_to_xai_input

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    auth_json = hermes_home / "auth.json"
    auth_json.write_text('{"api_key":"sk-secret"}', encoding="utf-8")
    image_link = hermes_home / "leak.png"
    try:
        image_link.symlink_to(auth_json)
    except OSError as exc:
        pytest.skip(f"symlink unavailable on this platform: {exc}")

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    with pytest.raises(ValueError, match="credential store"):
        _image_ref_to_xai_input(str(image_link))


