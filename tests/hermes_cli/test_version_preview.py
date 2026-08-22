from pathlib import Path


def test_deploy_preview_ranges_include_deploy_and_upstream(monkeypatch):
    from hermes_cli import axiom_update

    repo = Path("/tmp/hermes-preview")
    counts = {
        ("HEAD", "origin/axiom"): 5,
        ("origin/axiom", "upstream/main"): 25,
    }

    monkeypatch.setattr(axiom_update, "_current_git_branch", lambda _repo: "axiom")
    monkeypatch.setattr(axiom_update, "_has_git_remote", lambda _repo, name: name == "upstream")
    monkeypatch.setattr(
        axiom_update,
        "_count_git_range",
        lambda _repo, base, target: counts.get((base, target)),
    )

    assert axiom_update.get_update_preview_ranges(repo) == [
        ("HEAD", "origin/axiom", "Pending deploy branch changes"),
        ("origin/axiom", "upstream/main", "Pending upstream changes"),
    ]


def test_deploy_preview_ranges_skip_empty_hops(monkeypatch):
    from hermes_cli import axiom_update

    repo = Path("/tmp/hermes-preview")

    monkeypatch.setattr(axiom_update, "_current_git_branch", lambda _repo: "axiom")
    monkeypatch.setattr(axiom_update, "_has_git_remote", lambda _repo, name: name == "upstream")
    monkeypatch.setattr(
        axiom_update,
        "_count_git_range",
        lambda _repo, base, target: 0 if (base, target) == ("HEAD", "origin/axiom") else 7,
    )

    assert axiom_update.get_update_preview_ranges(repo) == [
        ("origin/axiom", "upstream/main", "Pending upstream changes"),
    ]


def test_version_preview_prints_all_deploy_digests(monkeypatch, tmp_path, capsys):
    from hermes_cli import axiom_update, update_ui

    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("HERMES_VERSION_NO_PREVIEW", "")
    monkeypatch.setattr(
        axiom_update,
        "get_update_preview_ranges",
        lambda _repo: [
            ("HEAD", "origin/axiom", "Pending deploy branch changes"),
            ("origin/axiom", "upstream/main", "Pending upstream changes"),
        ],
    )
    monkeypatch.setattr(
        update_ui,
        "compute_pending_digest",
        lambda _repo, _base, _target, *, title: f"DIGEST: {title}",
    )

    axiom_update.print_version_preview(tmp_path)

    out = capsys.readouterr().out
    assert "DIGEST: Pending deploy branch changes" in out
    assert "DIGEST: Pending upstream changes" in out


def test_fast_version_path_delegates_to_deploy_preview():
    from hermes_cli import _startup_fast

    names = _startup_fast.print_fast_version_info.__code__.co_names
    assert "print_version_preview" in names
