from pathlib import Path


def test_deploy_preview_ranges_include_deploy_and_upstream(monkeypatch):
    from hermes_cli import banner

    repo = Path("/tmp/hermes-preview")
    counts = {
        ("HEAD", "origin/axiom"): 5,
        ("origin/axiom", "upstream/main"): 25,
    }

    monkeypatch.setattr(banner, "_current_git_branch", lambda _repo: "axiom")
    monkeypatch.setattr(banner, "_has_git_remote", lambda _repo, name: name == "upstream")
    monkeypatch.setattr(
        banner,
        "_count_git_range",
        lambda _repo, base, target: counts.get((base, target)),
    )

    assert banner.get_update_preview_ranges(repo) == [
        ("HEAD", "origin/axiom", "Pending deploy branch changes"),
        ("origin/axiom", "upstream/main", "Pending upstream changes"),
    ]
    # Back-compat wrapper still returns the first preview range for older callers.
    assert banner.get_update_preview_range(repo) == (
        "HEAD",
        "origin/axiom",
        "Pending deploy branch changes",
    )


def test_deploy_preview_ranges_skip_empty_hops(monkeypatch):
    from hermes_cli import banner

    repo = Path("/tmp/hermes-preview")

    monkeypatch.setattr(banner, "_current_git_branch", lambda _repo: "axiom")
    monkeypatch.setattr(banner, "_has_git_remote", lambda _repo, name: name == "upstream")
    monkeypatch.setattr(
        banner,
        "_count_git_range",
        lambda _repo, base, target: 0 if (base, target) == ("HEAD", "origin/axiom") else 7,
    )

    assert banner.get_update_preview_ranges(repo) == [
        ("origin/axiom", "upstream/main", "Pending upstream changes"),
    ]


def test_version_info_prints_all_preview_digests(monkeypatch, capsys):
    from hermes_cli import banner, config, main, update_ui

    monkeypatch.delenv("HERMES_VERSION_NO_PREVIEW", raising=False)
    monkeypatch.setattr(banner, "format_banner_version_label", lambda: "Hermes Agent test")
    monkeypatch.setattr(banner, "check_for_updates", lambda: 30)
    monkeypatch.setattr(config, "recommended_update_command", lambda: "hermes update")
    monkeypatch.setattr(
        banner,
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

    main._print_version_info(check_updates=True)

    out = capsys.readouterr().out
    assert "Update available: 30 commits behind" in out
    assert "DIGEST: Pending deploy branch changes" in out
    assert "DIGEST: Pending upstream changes" in out
