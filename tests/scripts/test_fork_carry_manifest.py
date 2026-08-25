from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


REPO = Path(__file__).parents[2]
SCRIPT = REPO / "scripts" / "fork_carry_manifest.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fork_carry_manifest", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_manifest(repo: Path) -> dict[str, object]:
    (repo / "src").mkdir(exist_ok=True)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "src" / "feature.py").touch()
    (repo / "tests" / "test_feature.py").touch()
    return {
        "schema_version": 1,
        "carries": [
            {
                "id": "example-carry",
                "order": 10,
                "title": "Example carry",
                "status": "active",
                "domain_id": "example-domain",
                "ownership": "core",
                "contract": {"path": "FORK.md", "heading": "Example carry"},
                "depends_on": [],
                "provenance": [
                    {
                        "kind": "commit",
                        "repository": "example/repository",
                        "revision": "0123456789abcdef0123456789abcdef01234567",
                    }
                ],
                "summary": "Protect the example behavior.",
                "paths": ["src/feature.py"],
                "tests": ["tests/test_feature.py"],
                "checks": [
                    {
                        "id": "example-check",
                        "cwd": ".",
                        "argv": ["python", "-m", "pytest", "tests/test_feature.py"],
                        "env": {},
                        "covers": ["tests/test_feature.py"],
                    }
                ],
                "retirement": "Retire when upstream provides equivalent behavior.",
            }
        ],
    }


def test_minimal_valid_active_manifest(tmp_path: Path) -> None:
    module = load_module()
    manifest = minimal_manifest(tmp_path)

    assert module.validate_manifest(manifest, tmp_path) == []


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ([], "$: must be an object"),
        ({"schema_version": 2, "carries": []}, "schema_version: unsupported version 2"),
        ({"schema_version": 1}, "$: missing required field 'carries'"),
        ({"schema_version": 1, "carries": [], "extra": True}, "$: unknown field 'extra'"),
        ({"schema_version": "1", "carries": []}, "schema_version: must be integer 1"),
        ({"schema_version": 1, "carries": {}}, "carries: must be an array"),
    ],
)
def test_root_structure_is_strict(tmp_path: Path, manifest: object, expected: str) -> None:
    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert expected in diagnostics


def test_carry_requires_all_fields_and_rejects_unknown_fields(tmp_path: Path) -> None:
    module = load_module()
    manifest = minimal_manifest(tmp_path)
    carry = manifest["carries"][0]  # type: ignore[index]
    del carry["summary"]  # type: ignore[index]
    carry["surprise"] = True  # type: ignore[index]

    diagnostics = module.validate_manifest(manifest, tmp_path)

    assert "carries[0]: missing required field 'summary'" in diagnostics
    assert "carries[0]: unknown field 'surprise'" in diagnostics


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("id", 1, "carries[0].id: must be a string"),
        ("order", "10", "carries[0].order: must be a positive integer"),
        ("title", [], "carries[0].title: must be a string"),
        ("status", "pending", "carries[0].status: must be one of: active, retired"),
        ("domain_id", {}, "carries[0].domain_id: must be a string"),
        ("ownership", "external", "carries[0].ownership: must be one of: core, mixed, plugin"),
        ("contract", [], "carries[0].contract: must be an object"),
        ("depends_on", {}, "carries[0].depends_on: must be an array"),
        ("provenance", {}, "carries[0].provenance: must be an array"),
        ("summary", 1, "carries[0].summary: must be a string"),
        ("paths", {}, "carries[0].paths: must be an array"),
        ("tests", {}, "carries[0].tests: must be an array"),
        ("checks", {}, "carries[0].checks: must be an array"),
        ("retirement", None, "carries[0].retirement: must be a string"),
        ("references", {}, "carries[0].references: must be an array"),
        ("notes", {}, "carries[0].notes: must be an array"),
    ],
)
def test_carry_field_types_are_checked(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    manifest = minimal_manifest(tmp_path)
    carry = manifest["carries"][0]  # type: ignore[index]
    carry[field] = value  # type: ignore[index]

    assert expected in load_module().validate_manifest(manifest, tmp_path)


def test_diagnostics_are_deterministic(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    bad = copy.deepcopy(manifest)
    carry = bad["carries"][0]  # type: ignore[index]
    carry["summary"] = " "  # type: ignore[index]
    carry["unknown"] = 1  # type: ignore[index]
    module = load_module()

    assert module.validate_manifest(bad, tmp_path) == module.validate_manifest(bad, tmp_path)


def two_carry_manifest(repo: Path) -> dict[str, object]:
    manifest = minimal_manifest(repo)
    first = manifest["carries"][0]  # type: ignore[index]
    second = copy.deepcopy(first)
    second["id"] = "second-carry"  # type: ignore[index]
    second["order"] = 20  # type: ignore[index]
    second["checks"][0]["id"] = "second-check"  # type: ignore[index]
    manifest["carries"].append(second)  # type: ignore[union-attr]
    return manifest


@pytest.mark.parametrize("field", ["id", "domain_id"])
@pytest.mark.parametrize("value", ["Upper-Case", "under_score", "-leading", "trailing-"])
def test_identifiers_require_lowercase_kebab(
    tmp_path: Path, field: str, value: str
) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0][field] = value  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert f"carries[0].{field}: must be lowercase kebab-case" in diagnostics


def test_carry_ids_and_orders_are_unique(tmp_path: Path) -> None:
    manifest = two_carry_manifest(tmp_path)
    manifest["carries"][1]["id"] = "example-carry"  # type: ignore[index]
    manifest["carries"][1]["order"] = 10  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[1].id: duplicate carry id 'example-carry'" in diagnostics
    assert "carries[1].order: duplicate order 10" in diagnostics


def test_array_order_must_be_strictly_increasing(tmp_path: Path) -> None:
    manifest = two_carry_manifest(tmp_path)
    manifest["carries"][1]["order"] = 5  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[1].order: must be greater than previous order 10" in diagnostics


@pytest.mark.parametrize(
    ("dependencies", "expected"),
    [
        (["example-carry", "example-carry"], "duplicate dependency 'example-carry'"),
        (["second-carry"], "self-dependency 'second-carry'"),
        (["missing-carry"], "unknown dependency 'missing-carry'"),
        (["Bad_ID"], "must be lowercase kebab-case"),
    ],
)
def test_dependencies_reject_duplicates_self_and_missing(
    tmp_path: Path, dependencies: list[str], expected: str
) -> None:
    manifest = two_carry_manifest(tmp_path)
    manifest["carries"][1]["depends_on"] = dependencies  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert any(expected in diagnostic for diagnostic in diagnostics)


def test_dependencies_must_appear_earlier(tmp_path: Path) -> None:
    manifest = two_carry_manifest(tmp_path)
    manifest["carries"][0]["depends_on"] = ["second-carry"]  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].depends_on[0]: dependency 'second-carry' must appear earlier" in diagnostics


def test_cycle_is_reported_even_with_forward_dependency(tmp_path: Path) -> None:
    manifest = two_carry_manifest(tmp_path)
    manifest["carries"][0]["depends_on"] = ["second-carry"]  # type: ignore[index]
    manifest["carries"][1]["depends_on"] = ["example-carry"]  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].depends_on[0]: dependency 'second-carry' must appear earlier" in diagnostics
    assert any("dependency cycle:" in diagnostic for diagnostic in diagnostics)


@pytest.mark.parametrize(
    "value",
    [
        "/absolute/path",
        "C:/absolute/path",
        "dir\\file.py",
        ".",
        "./file.py",
        "dir/../file.py",
        "dir//file.py",
        "dir/*.py",
        "dir/file?.py",
        "dir/[ab].py",
        "dir/{a,b}.py",
    ],
)
def test_repo_paths_reject_non_exact_syntax(tmp_path: Path, value: str) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["paths"] = [value]  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert any("carries[0].paths[0]: invalid repository-relative POSIX path" in item for item in diagnostics)


def test_repo_paths_reject_duplicates(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["tests"] = [  # type: ignore[index]
        "tests/test_feature.py",
        "tests/test_feature.py",
    ]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].tests[1]: duplicate path 'tests/test_feature.py'" in diagnostics


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("paths", "active carry requires at least one protected path"),
        ("tests", "active carry requires at least one test path"),
        ("checks", "active carry requires at least one check"),
    ],
)
def test_active_carries_require_nonempty_artifacts(
    tmp_path: Path, field: str, expected: str
) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0][field] = []  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert f"carries[0].{field}: {expected}" in diagnostics


def test_active_paths_tests_and_check_cwd_must_exist(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    carry = manifest["carries"][0]  # type: ignore[index]
    carry["paths"] = ["src/missing.py"]  # type: ignore[index]
    carry["tests"] = ["tests/missing.py"]  # type: ignore[index]
    carry["checks"][0]["cwd"] = "missing-directory"  # type: ignore[index]
    carry["checks"][0]["covers"] = ["tests/missing.py"]  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].paths[0]: path does not exist" in diagnostics
    assert "carries[0].tests[0]: path does not exist" in diagnostics
    assert "carries[0].checks[0].cwd: directory does not exist" in diagnostics


def test_path_existence_can_be_deferred_until_candidate_tree(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    carry = manifest["carries"][0]  # type: ignore[index]
    carry["paths"] = ["src/missing.py"]  # type: ignore[index]
    carry["tests"] = ["tests/missing.py"]  # type: ignore[index]

    diagnostics = load_module().validate_manifest(
        manifest, tmp_path, check_path_existence=False
    )

    assert not any("path does not exist" in item for item in diagnostics)


def test_retired_carry_allows_empty_and_missing_historical_artifacts(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    carry = manifest["carries"][0]  # type: ignore[index]
    carry["status"] = "retired"  # type: ignore[index]
    carry["paths"] = ["removed/feature.py"]  # type: ignore[index]
    carry["tests"] = []  # type: ignore[index]
    carry["checks"] = []  # type: ignore[index]

    assert load_module().validate_manifest(manifest, tmp_path) == []


def test_path_resolution_cannot_escape_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path.parent / "outside-carry-file.py"
    outside.touch()
    link = tmp_path / "escape-link"
    try:
        link.symlink_to(tmp_path.parent, target_is_directory=True)
    except OSError:
        original_resolve = Path.resolve
        escaped = link / outside.name

        def fake_resolve(path: Path, *args: object, **kwargs: object) -> Path:
            if path == escaped:
                return outside
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", fake_resolve)
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["paths"] = [  # type: ignore[index]
        "escape-link/outside-carry-file.py"
    ]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].paths[0]: path resolves outside repository" in diagnostics


def test_check_shape_is_strict(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    check = manifest["carries"][0]["checks"][0]  # type: ignore[index]
    del check["env"]  # type: ignore[index]
    check["extra"] = True  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].checks[0]: missing required field 'env'" in diagnostics
    assert "carries[0].checks[0]: unknown field 'extra'" in diagnostics


def test_check_ids_are_global_unique_kebab(tmp_path: Path) -> None:
    manifest = two_carry_manifest(tmp_path)
    manifest["carries"][0]["checks"][0]["id"] = "Bad_ID"  # type: ignore[index]
    manifest["carries"][1]["checks"][0]["id"] = "Bad_ID"  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].checks[0].id: must be lowercase kebab-case" in diagnostics
    assert "carries[1].checks[0].id: duplicate global check id 'Bad_ID'" in diagnostics


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ("python -m pytest", "must be a nonempty array of nonblank strings"),
        ([], "must be a nonempty array of nonblank strings"),
        (["python", " "], "argv[1]: must be a nonblank string"),
    ],
)
def test_check_argv_is_an_argument_vector(
    tmp_path: Path, argv: object, expected: str
) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["checks"][0]["argv"] = argv  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert any(expected in diagnostic for diagnostic in diagnostics)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ([], "env: must be an object"),
        ({"": "value"}, "env: keys must be nonempty strings"),
        ({"KEY": 1}, "env.KEY: must be a string"),
    ],
)
def test_check_env_is_a_string_map(tmp_path: Path, env: object, expected: str) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["checks"][0]["env"] = env  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert any(expected in diagnostic for diagnostic in diagnostics)


def test_check_covers_must_be_unique_subset_of_tests(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    covers = ["tests/not-declared.py", "tests/not-declared.py"]
    manifest["carries"][0]["checks"][0]["covers"] = covers  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].checks[0].covers[0]: not declared in carry tests" in diagnostics
    assert "carries[0].checks[0].covers[1]: duplicate covered test 'tests/not-declared.py'" in diagnostics


def test_every_active_test_must_be_covered(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["checks"][0]["covers"] = []  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].tests[0]: active test is not covered by any check" in diagnostics


@pytest.mark.parametrize("field", ["title", "summary", "retirement"])
def test_required_text_fields_are_nonblank(tmp_path: Path, field: str) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0][field] = " \t"  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert f"carries[0].{field}: must be nonblank" in diagnostics


def test_contract_shape_path_and_heading_are_strict(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["contract"] = {  # type: ignore[index]
        "path": "../FORK.md",
        "heading": " ",
        "extra": True,
    }

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].contract: unknown field 'extra'" in diagnostics
    assert any("carries[0].contract.path: invalid repository-relative POSIX path" in item for item in diagnostics)
    assert "carries[0].contract.heading: must be nonblank" in diagnostics


def test_optional_references_and_notes_are_string_arrays(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    carry = manifest["carries"][0]  # type: ignore[index]
    carry["references"] = ["reference", 1]  # type: ignore[index]
    carry["notes"] = [None]  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert "carries[0].references[1]: must be a string" in diagnostics
    assert "carries[0].notes[0]: must be a string" in diagnostics


def test_optional_commit_series_replay_metadata_is_valid(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["replay"] = {  # type: ignore[index]
        "kind": "commit_series",
        "source_ref": "origin/carry/example-carry",
        "base_commit": "1" * 40,
        "commits": ["2" * 40, "3" * 40],
    }

    assert load_module().validate_manifest(manifest, tmp_path) == []


def test_mutable_candidate_ref_is_rejected_as_replay_source(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["replay"] = {  # type: ignore[index]
        "kind": "commit_series",
        "source_ref": "origin/axiom-next",
        "base_commit": "1" * 40,
        "commits": ["2" * 40],
    }

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert any("must not use mutable candidate ref" in row for row in diagnostics)


@pytest.mark.parametrize(
    ("replay", "expected"),
    [
        ([], "replay: must be an object"),
        ({"kind": "tag", "source_ref": "x", "base_commit": "1" * 40, "commits": ["2" * 40]}, "replay.kind: must be 'commit_series'"),
        ({"kind": "commit_series", "source_ref": " ", "base_commit": "1" * 40, "commits": ["2" * 40]}, "replay.source_ref: must be nonblank"),
        ({"kind": "commit_series", "source_ref": "x", "base_commit": "short", "commits": ["2" * 40]}, "replay.base_commit: must be exactly 40 lowercase hexadecimal characters"),
        ({"kind": "commit_series", "source_ref": "x", "base_commit": "1" * 40, "commits": []}, "replay.commits: must be a nonempty array"),
        ({"kind": "commit_series", "source_ref": "x", "base_commit": "1" * 40, "commits": ["2" * 40, "2" * 40]}, "duplicate commit"),
        ({"kind": "commit_series", "source_ref": "x", "base_commit": "1" * 40, "commits": ["G" * 40]}, "must be exactly 40 lowercase hexadecimal characters"),
        ({"kind": "commit_series", "source_ref": "x", "base_commit": "1" * 40, "commits": ["2" * 40], "extra": True}, "replay: unknown field 'extra'"),
    ],
)
def test_invalid_replay_metadata_is_rejected(
    tmp_path: Path, replay: object, expected: str
) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["replay"] = replay  # type: ignore[index]

    assert any(expected in item for item in load_module().validate_manifest(manifest, tmp_path))


def test_all_provenance_kinds_are_valid(tmp_path: Path) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["provenance"] = [  # type: ignore[index]
        {
            "kind": "commit",
            "repository": "example/repository",
            "revision": "abcdef0123456789abcdef0123456789abcdef01",
        },
        {"kind": "pull_request", "repository": "example/repository", "number": 7},
        {"kind": "manual", "description": "Locally maintained behavior."},
    ]

    assert load_module().validate_manifest(manifest, tmp_path) == []


@pytest.mark.parametrize(
    ("provenance", "expected"),
    [
        ([], "provenance: must contain at least one entry"),
        ([{"kind": "tag"}], "provenance[0].kind: unsupported provenance kind 'tag'"),
        (
            [{"kind": "commit", "repository": "repo", "revision": "abc123"}],
            "revision: must be exactly 40 hexadecimal characters",
        ),
        (
            [{"kind": "commit", "repository": " ", "revision": "g" * 40}],
            "repository: must be nonblank",
        ),
        (
            [{"kind": "pull_request", "repository": "repo", "number": 0}],
            "number: must be a positive integer",
        ),
        (
            [{"kind": "manual", "description": " "}],
            "description: must be nonblank",
        ),
        (
            [{"kind": "manual", "description": "reason", "repository": "extra"}],
            "unknown field 'repository'",
        ),
        (
            [{"kind": "commit", "repository": "repo"}],
            "missing required field 'revision'",
        ),
    ],
)
def test_invalid_provenance_is_rejected(
    tmp_path: Path, provenance: list[object], expected: str
) -> None:
    manifest = minimal_manifest(tmp_path)
    manifest["carries"][0]["provenance"] = provenance  # type: ignore[index]

    diagnostics = load_module().validate_manifest(manifest, tmp_path)

    assert any(expected in diagnostic for diagnostic in diagnostics)


def write_cli_manifest(path: Path, repo: Path, *, two: bool = False) -> dict[str, object]:
    manifest = two_carry_manifest(repo) if two else minimal_manifest(repo)
    for carry in manifest["carries"]:  # type: ignore[union-attr]
        carry["status"] = "retired"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_status_and_markdown_preserve_manifest_order(tmp_path: Path) -> None:
    manifest = two_carry_manifest(tmp_path)
    module = load_module()

    status = module.build_status(manifest, [])
    markdown = module.render_markdown(status)

    assert status["total"] == len(status["carries"]) == 2
    assert status["active"] == 2
    assert status["retired"] == 0
    assert status["declared_checks"] == 2
    assert [row["id"] for row in status["carries"]] == ["example-carry", "second-carry"]
    assert status["carries"][1]["check_ids"] == ["second-check"]
    assert markdown.index("example-carry") < markdown.index("second-carry")
    assert str(tmp_path) not in markdown


def test_status_rendering_is_byte_deterministic(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    write_cli_manifest(manifest_path, tmp_path, two=True)

    markdown_a = run_cli("status", "--manifest", str(manifest_path))
    markdown_b = run_cli("status", "--manifest", str(manifest_path))
    json_a = run_cli("status", "--manifest", str(manifest_path), "--json")
    json_b = run_cli("status", "--manifest", str(manifest_path), "--json")

    assert markdown_a.returncode == markdown_b.returncode == 0
    assert json_a.returncode == json_b.returncode == 0
    assert markdown_a.stdout == markdown_b.stdout
    assert json_a.stdout == json_b.stdout
    assert json_a.stdout == json.dumps(json.loads(json_a.stdout), indent=2, sort_keys=True) + "\n"
    assert str(tmp_path) not in markdown_a.stdout
    assert str(tmp_path) not in json_a.stdout


def test_validate_and_status_never_execute_check_argv(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = write_cli_manifest(manifest_path, tmp_path)
    marker = tmp_path / "must-not-exist"
    manifest["carries"][0]["checks"][0]["argv"] = [  # type: ignore[index]
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).touch()",
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    for command in ("validate", "status"):
        for output_flag in ((), ("--json",)):
            result = run_cli(command, "--manifest", str(manifest_path), *output_flag)
            assert result.returncode == 0
    assert not marker.exists()


def test_cli_exit_codes_and_error_channels(tmp_path: Path) -> None:
    valid_path = tmp_path / "valid.json"
    invalid_path = tmp_path / "invalid.json"
    malformed_path = tmp_path / "malformed.json"
    manifest = write_cli_manifest(valid_path, tmp_path)
    manifest["schema_version"] = 2
    invalid_path.write_text(json.dumps(manifest), encoding="utf-8")
    malformed_path.write_text("{", encoding="utf-8")

    assert run_cli("validate", "--manifest", str(valid_path)).returncode == 0
    assert run_cli("validate", "--manifest", str(invalid_path)).returncode == 1
    for path in (malformed_path, tmp_path / "missing.json"):
        result = run_cli("status", "--manifest", str(path), "--json")
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr


def test_import_has_no_output_or_side_effects(capsys: pytest.CaptureFixture[str]) -> None:
    load_module()

    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_schema_matches_locked_v1_structure() -> None:
    schema = json.loads((REPO / "fork-carries.schema.json").read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["schema_version", "carries"]
    assert schema["properties"]["schema_version"] == {"const": 1, "type": "integer"}
    carry = schema["$defs"]["carry"]
    assert carry["additionalProperties"] is False
    assert set(carry["required"]) == set(load_module().CARRY_REQUIRED)
    assert carry["properties"]["ownership"]["enum"] == ["core", "plugin", "mixed"]
    assert carry["allOf"][0]["if"]["properties"]["status"]["const"] == "active"
    for field in ("paths", "tests", "checks"):
        assert carry["allOf"][0]["then"]["properties"][field]["minItems"] == 1
    for definition in ("contract", "commit", "pull_request", "manual", "check", "replay"):
        assert schema["$defs"][definition]["additionalProperties"] is False
    replay = schema["$defs"]["replay"]
    assert replay["properties"]["kind"] == {"const": "commit_series"}
    assert replay["properties"]["commits"]["minItems"] == 1
    assert replay["properties"]["commits"]["uniqueItems"] is True
    assert schema["$defs"]["commit"]["properties"]["revision"]["pattern"] == "^[0-9a-fA-F]{40}$"
    provenance = carry["properties"]["provenance"]
    assert provenance["minItems"] == 1
    assert len(provenance["items"]["oneOf"]) == 3
    env = schema["$defs"]["check"]["properties"]["env"]
    assert env["propertyNames"]["minLength"] == 1
    assert env["additionalProperties"] == {"type": "string"}


def test_committed_manifest_validates_cleanly() -> None:
    module = load_module()
    manifest = module.load_manifest(REPO / "fork-carries.json")

    assert module.validate_manifest(manifest, REPO) == []
