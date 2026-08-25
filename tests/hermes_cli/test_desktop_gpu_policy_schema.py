from hermes_cli.web_server import _SCHEMA_OVERRIDES


def test_desktop_gpu_policy_schema_preserves_auto_and_both_explicit_overrides():
    field = _SCHEMA_OVERRIDES["desktop.disable_gpu"]

    assert field["type"] == "select"
    assert field["options"] == ["auto", "false", "true"]
    assert field["category"] == "display"
    assert "restarting Hermes Desktop" in field["description"]