"""The commit_count == 0 path must repair Node deps, not just Python (#77211).

A previous ``hermes update`` whose npm install failed printed "Fix npm and
re-run `hermes update`" — but re-running hit the "Already up to date!" early
return before the Node refresh, so the advice could never work. The repair
now runs through ``_repair_node_deps_on_current_checkout``, which delegates
to ``_update_node_dependencies`` (self-gating on the lockfile hash, recorded
only after a successful install, so healthy installs stay a cheap no-op).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermes_cli import update_cmd


def test_current_checkout_repairs_failed_node_deps(capsys):
    """A recorded failure surfaces the fix-npm hint, not 'Already up to date!'."""
    completion = MagicMock()
    with patch.object(
        update_cmd, "_update_node_dependencies", return_value=["ui-tui, web workspaces"]
    ), patch.object(update_cmd, "_m") as m:
        update_cmd._repair_node_deps_on_current_checkout(completion)

    m.return_value._build_web_ui.assert_not_called()
    completion.assert_not_called()
    out = capsys.readouterr().out
    assert "could not be repaired" in out
    assert "Node.js refresh failed for: ui-tui, web workspaces" in out
    assert "Fix npm and re-run `hermes update`." in out


def test_current_checkout_healthy_node_deps_reports_up_to_date():
    """A clean refresh (or lockfile-hash no-op) still says 'Already up to date!'."""
    completion = MagicMock()
    with patch.object(
        update_cmd, "_update_node_dependencies", return_value=[]
    ), patch.object(update_cmd, "_desktop_install_intent", return_value=True), patch.object(
        update_cmd, "_rebuild_desktop_after_update"
    ) as reconcile, patch.object(update_cmd, "_m") as m:
        update_cmd._repair_node_deps_on_current_checkout(completion)

    # The refresh pairs with the web build like every other call site.
    m.return_value._build_web_ui.assert_called_once()
    desktop_dir = m.return_value.PROJECT_ROOT / "apps" / "desktop"
    reconcile.assert_called_once_with(
        desktop_dir,
        had_desktop_app_before_update=True,
    )
    completion.assert_called_once_with("✓ Already up to date!")


def test_current_checkout_reports_stale_desktop_when_repair_fails(capsys):
    completion = MagicMock()
    with patch.object(
        update_cmd, "_update_node_dependencies", return_value=[]
    ), patch.object(update_cmd, "_desktop_install_intent", return_value=True), patch.object(
        update_cmd, "_rebuild_desktop_after_update", return_value=False
    ), patch.object(update_cmd, "_m") as m:
        repair_ok = update_cmd._repair_node_deps_on_current_checkout(completion)

    assert repair_ok is False
    m.return_value._build_web_ui.assert_called_once()
    completion.assert_not_called()
    message = capsys.readouterr().out
    assert "Desktop" in message
    assert "stale" in message.lower()
    assert "Already up to date" not in message
