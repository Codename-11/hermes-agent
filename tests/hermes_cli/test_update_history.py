import json
from pathlib import Path

from hermes_cli import update_ui


def _commits(count: int):
    return [
        (
            f"{index:040x}",
            f"fix(desktop): repair update path {index}",
            "Hermes",
            "fix",
        )
        for index in range(count)
    ]


def test_write_update_brief_records_structured_history(tmp_path, monkeypatch):
    briefs = tmp_path / "logs" / "update-briefs"
    briefs.mkdir(parents=True)
    monkeypatch.setattr(update_ui, "_briefs_dir", lambda: briefs)
    monkeypatch.setattr(
        update_ui,
        "_collect_commits",
        lambda *_args, **_kwargs: (_commits(2), "2 files changed, 8 insertions(+)", ["a.py", "b.ts"]),
    )

    brief = update_ui.write_update_brief(
        Path("/repo"),
        "a" * 40,
        "b" * 40,
        branch="axiom",
    )

    assert brief is not None
    sidecar = Path(brief.meta["history_entry_path"])
    history_path = briefs.parent / "update-history.json"
    assert sidecar.exists()
    assert history_path.exists()

    entry = json.loads(sidecar.read_text(encoding="utf-8"))
    history = json.loads(history_path.read_text(encoding="utf-8"))

    assert entry["status"] == "completed"
    assert entry["branch"] == "axiom"
    assert entry["oldSha"] == "a" * 40
    assert entry["newSha"] == "b" * 40
    assert entry["summary"] == "2 fixes"
    assert entry["filesChanged"] == ["a.py", "b.ts"]
    assert entry["commits"][0] == {
        "sha": "0" * 40,
        "summary": "fix(desktop): repair update path 0",
        "author": "Hermes",
        "category": "fix",
    }
    assert history == [entry]


def test_append_update_history_is_bounded_and_recovers_from_malformed_index(tmp_path):
    history_path = tmp_path / "update-history.json"
    history_path.write_text("not-json", encoding="utf-8")

    for index in range(55):
        update_ui._append_update_history(
            history_path,
            {"id": str(index), "finishedAt": index},
        )

    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(history) == 50
    assert history[0]["id"] == "54"
    assert history[-1]["id"] == "5"
