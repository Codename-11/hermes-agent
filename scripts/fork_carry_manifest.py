#!/usr/bin/env python3
"""Validate and report the static fork carry manifest without executing checks."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "fork-carries.json"

ROOT_REQUIRED = ("schema_version", "carries")
CARRY_REQUIRED = (
    "id",
    "order",
    "title",
    "status",
    "domain_id",
    "ownership",
    "contract",
    "depends_on",
    "provenance",
    "summary",
    "paths",
    "tests",
    "checks",
    "retirement",
)
CARRY_OPTIONAL = ("references", "notes", "replay")
CHECK_REQUIRED = ("id", "cwd", "argv", "env", "covers")
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load_manifest(path: Path | str = DEFAULT_MANIFEST) -> Any:
    """Load and return a JSON manifest."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _object_shape(
    value: Any,
    location: str,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{location}: must be an object"]
    diagnostics = [
        f"{location}: missing required field '{field}'"
        for field in required
        if field not in value
    ]
    allowed = set(required) | set(optional)
    diagnostics.extend(
        f"{location}: unknown field '{field}'" for field in sorted(value) if field not in allowed
    )
    return diagnostics


def _field_type_diagnostics(carry: dict[str, Any], location: str) -> list[str]:
    diagnostics: list[str] = []
    for field in ("id", "title", "domain_id", "summary", "retirement"):
        if field in carry and not isinstance(carry[field], str):
            diagnostics.append(f"{location}.{field}: must be a string")
    if "order" in carry and (
        not isinstance(carry["order"], int)
        or isinstance(carry["order"], bool)
        or carry["order"] <= 0
    ):
        diagnostics.append(f"{location}.order: must be a positive integer")
    if "status" in carry and carry["status"] not in ("active", "retired"):
        diagnostics.append(f"{location}.status: must be one of: active, retired")
    if "ownership" in carry and carry["ownership"] not in ("core", "mixed", "plugin"):
        diagnostics.append(f"{location}.ownership: must be one of: core, mixed, plugin")
    if "contract" in carry and not isinstance(carry["contract"], dict):
        diagnostics.append(f"{location}.contract: must be an object")
    if "replay" in carry and not isinstance(carry["replay"], dict):
        diagnostics.append(f"{location}.replay: must be an object")
    for field in ("depends_on", "provenance", "paths", "tests", "checks", "references", "notes"):
        if field in carry and not isinstance(carry[field], list):
            diagnostics.append(f"{location}.{field}: must be an array")
    return diagnostics


def _valid_repo_path(value: str, *, allow_dot: bool = False) -> bool:
    if allow_dot and value == ".":
        return True
    if not value or value == "." or value.startswith("/") or "\\" in value:
        return False
    if re.match(r"^[A-Za-z]:", value) or any(char in value for char in "*?[]{}"):
        return False
    return all(segment not in ("", ".", "..") for segment in value.split("/"))


def _path_diagnostics(carries: list[Any], repo_root: Path) -> list[str]:
    diagnostics: list[str] = []
    root = repo_root.resolve()
    for carry_index, carry in enumerate(carries):
        if not isinstance(carry, dict):
            continue
        active = carry.get("status") == "active"
        for field, empty_message in (
            ("paths", "active carry requires at least one protected path"),
            ("tests", "active carry requires at least one test path"),
        ):
            values = carry.get(field)
            if not isinstance(values, list):
                continue
            if active and not values:
                diagnostics.append(f"carries[{carry_index}].{field}: {empty_message}")
            seen: set[str] = set()
            for value_index, value in enumerate(values):
                location = f"carries[{carry_index}].{field}[{value_index}]"
                if not isinstance(value, str):
                    diagnostics.append(f"{location}: must be a string")
                    continue
                if value in seen:
                    diagnostics.append(f"{location}: duplicate path '{value}'")
                seen.add(value)
                if not _valid_repo_path(value):
                    diagnostics.append(
                        f"{location}: invalid repository-relative POSIX path '{value}'"
                    )
                    continue
                target = (root / value).resolve()
                if not target.is_relative_to(root):
                    diagnostics.append(f"{location}: path resolves outside repository")
                elif active and not target.exists():
                    diagnostics.append(f"{location}: path does not exist")
        checks = carry.get("checks")
        if isinstance(checks, list):
            if active and not checks:
                diagnostics.append(
                    f"carries[{carry_index}].checks: active carry requires at least one check"
                )
            for check_index, check in enumerate(checks):
                if not isinstance(check, dict) or not isinstance(check.get("cwd"), str):
                    continue
                cwd = check["cwd"]
                location = f"carries[{carry_index}].checks[{check_index}].cwd"
                if not _valid_repo_path(cwd, allow_dot=True):
                    diagnostics.append(
                        f"{location}: invalid repository-relative POSIX path '{cwd}'"
                    )
                    continue
                target = (root / cwd).resolve()
                if not target.is_relative_to(root):
                    diagnostics.append(f"{location}: path resolves outside repository")
                elif active and not target.is_dir():
                    diagnostics.append(f"{location}: directory does not exist")
    return diagnostics


def _content_diagnostics(carries: list[Any]) -> list[str]:
    diagnostics: list[str] = []
    provenance_shapes = {
        "commit": ("kind", "repository", "revision"),
        "pull_request": ("kind", "repository", "number"),
        "manual": ("kind", "description"),
    }
    for carry_index, carry in enumerate(carries):
        if not isinstance(carry, dict):
            continue
        base = f"carries[{carry_index}]"
        for field in ("title", "summary", "retirement"):
            value = carry.get(field)
            if isinstance(value, str) and not value.strip():
                diagnostics.append(f"{base}.{field}: must be nonblank")
        contract = carry.get("contract")
        if isinstance(contract, dict):
            location = f"{base}.contract"
            diagnostics.extend(_object_shape(contract, location, ("path", "heading")))
            path = contract.get("path")
            if "path" in contract and not isinstance(path, str):
                diagnostics.append(f"{location}.path: must be a string")
            elif isinstance(path, str) and not _valid_repo_path(path):
                diagnostics.append(
                    f"{location}.path: invalid repository-relative POSIX path '{path}'"
                )
            heading = contract.get("heading")
            if "heading" in contract and not isinstance(heading, str):
                diagnostics.append(f"{location}.heading: must be a string")
            elif isinstance(heading, str) and not heading.strip():
                diagnostics.append(f"{location}.heading: must be nonblank")
        for field in ("references", "notes"):
            values = carry.get(field)
            if isinstance(values, list):
                for value_index, value in enumerate(values):
                    if not isinstance(value, str):
                        diagnostics.append(f"{base}.{field}[{value_index}]: must be a string")
        replay = carry.get("replay")
        if isinstance(replay, dict):
            location = f"{base}.replay"
            diagnostics.extend(
                _object_shape(
                    replay,
                    location,
                    ("kind", "source_ref", "base_commit", "commits"),
                )
            )
            if replay.get("kind") != "commit_series":
                diagnostics.append(f"{location}.kind: must be 'commit_series'")
            source_ref = replay.get("source_ref")
            if not isinstance(source_ref, str) or not source_ref.strip():
                diagnostics.append(f"{location}.source_ref: must be nonblank")
            elif (
                carry.get("status") == "active"
                and source_ref.strip().rsplit("/", 1)[-1].endswith("-next")
            ):
                diagnostics.append(
                    f"{location}.source_ref: must not use mutable candidate ref '*-next'"
                )
            base_commit = replay.get("base_commit")
            if not isinstance(base_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", base_commit):
                diagnostics.append(
                    f"{location}.base_commit: must be exactly 40 lowercase hexadecimal characters"
                )
            commits = replay.get("commits")
            if not isinstance(commits, list) or not commits:
                diagnostics.append(f"{location}.commits: must be a nonempty array")
            else:
                seen_commits: set[str] = set()
                for commit_index, commit in enumerate(commits):
                    commit_location = f"{location}.commits[{commit_index}]"
                    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                        diagnostics.append(
                            f"{commit_location}: must be exactly 40 lowercase hexadecimal characters"
                        )
                    if isinstance(commit, str) and commit in seen_commits:
                        diagnostics.append(f"{commit_location}: duplicate commit '{commit}'")
                    if isinstance(commit, str):
                        seen_commits.add(commit)
        provenance = carry.get("provenance")
        if not isinstance(provenance, list):
            continue
        if not provenance:
            diagnostics.append(f"{base}.provenance: must contain at least one entry")
        for provenance_index, entry in enumerate(provenance):
            location = f"{base}.provenance[{provenance_index}]"
            if not isinstance(entry, dict):
                diagnostics.append(f"{location}: must be an object")
                continue
            kind = entry.get("kind")
            if not isinstance(kind, str):
                diagnostics.append(f"{location}.kind: must be a string")
                continue
            required = provenance_shapes.get(kind)
            if required is None:
                diagnostics.append(f"{location}.kind: unsupported provenance kind '{kind}'")
                continue
            diagnostics.extend(_object_shape(entry, location, required))
            if kind in ("commit", "pull_request"):
                repository = entry.get("repository")
                if "repository" in entry and not isinstance(repository, str):
                    diagnostics.append(f"{location}.repository: must be a string")
                elif isinstance(repository, str) and not repository.strip():
                    diagnostics.append(f"{location}.repository: must be nonblank")
            if kind == "commit":
                revision = entry.get("revision")
                if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
                    if "revision" in entry:
                        diagnostics.append(
                            f"{location}.revision: must be exactly 40 hexadecimal characters"
                        )
            elif kind == "pull_request":
                number = entry.get("number")
                if (
                    "number" in entry
                    and (
                        not isinstance(number, int)
                        or isinstance(number, bool)
                        or number <= 0
                    )
                ):
                    diagnostics.append(f"{location}.number: must be a positive integer")
            else:
                description = entry.get("description")
                if "description" in entry and not isinstance(description, str):
                    diagnostics.append(f"{location}.description: must be a string")
                elif isinstance(description, str) and not description.strip():
                    diagnostics.append(f"{location}.description: must be nonblank")
    return diagnostics


def _check_diagnostics(carries: list[Any]) -> list[str]:
    diagnostics: list[str] = []
    global_ids: set[str] = set()
    for carry_index, carry in enumerate(carries):
        if not isinstance(carry, dict) or not isinstance(carry.get("checks"), list):
            continue
        tests = carry.get("tests") if isinstance(carry.get("tests"), list) else []
        declared_tests = {item for item in tests if isinstance(item, str)}
        covered_tests: set[str] = set()
        for check_index, check in enumerate(carry["checks"]):
            location = f"carries[{carry_index}].checks[{check_index}]"
            diagnostics.extend(_object_shape(check, location, CHECK_REQUIRED))
            if not isinstance(check, dict):
                continue
            check_id = check.get("id")
            if not isinstance(check_id, str):
                if "id" in check:
                    diagnostics.append(f"{location}.id: must be a string")
            else:
                if not KEBAB_RE.fullmatch(check_id):
                    diagnostics.append(f"{location}.id: must be lowercase kebab-case")
                if check_id in global_ids:
                    diagnostics.append(
                        f"{location}.id: duplicate global check id '{check_id}'"
                    )
                global_ids.add(check_id)
            cwd = check.get("cwd")
            if "cwd" in check and not isinstance(cwd, str):
                diagnostics.append(f"{location}.cwd: must be a string")
            argv = check.get("argv")
            if not isinstance(argv, list) or not argv:
                if "argv" in check:
                    diagnostics.append(
                        f"{location}.argv: must be a nonempty array of nonblank strings"
                    )
            else:
                for arg_index, argument in enumerate(argv):
                    if not isinstance(argument, str) or not argument.strip():
                        diagnostics.append(
                            f"{location}.argv[{arg_index}]: must be a nonblank string"
                        )
            env = check.get("env")
            if "env" in check and not isinstance(env, dict):
                diagnostics.append(f"{location}.env: must be an object")
            elif isinstance(env, dict):
                for key, value in env.items():
                    if not isinstance(key, str) or not key:
                        diagnostics.append(f"{location}.env: keys must be nonempty strings")
                    elif not isinstance(value, str):
                        diagnostics.append(f"{location}.env.{key}: must be a string")
            covers = check.get("covers")
            if "covers" in check and not isinstance(covers, list):
                diagnostics.append(f"{location}.covers: must be an array")
            elif isinstance(covers, list):
                seen: set[str] = set()
                for cover_index, covered in enumerate(covers):
                    cover_location = f"{location}.covers[{cover_index}]"
                    if not isinstance(covered, str):
                        diagnostics.append(f"{cover_location}: must be a string")
                        continue
                    if covered in seen:
                        diagnostics.append(
                            f"{cover_location}: duplicate covered test '{covered}'"
                        )
                    seen.add(covered)
                    if not _valid_repo_path(covered):
                        diagnostics.append(
                            f"{cover_location}: invalid repository-relative POSIX path '{covered}'"
                        )
                    if covered not in declared_tests:
                        diagnostics.append(f"{cover_location}: not declared in carry tests")
                    else:
                        covered_tests.add(covered)
        if carry.get("status") == "active":
            for test_index, test in enumerate(tests):
                if isinstance(test, str) and test not in covered_tests:
                    diagnostics.append(
                        f"carries[{carry_index}].tests[{test_index}]: "
                        "active test is not covered by any check"
                    )
    return diagnostics


def _identifier_and_dependency_diagnostics(carries: list[Any]) -> list[str]:
    diagnostics: list[str] = []
    id_indices: dict[str, int] = {}
    orders: set[int] = set()
    previous_order: int | None = None
    for index, carry in enumerate(carries):
        if not isinstance(carry, dict):
            continue
        location = f"carries[{index}]"
        for field in ("id", "domain_id"):
            value = carry.get(field)
            if isinstance(value, str) and not KEBAB_RE.fullmatch(value):
                diagnostics.append(f"{location}.{field}: must be lowercase kebab-case")
        carry_id = carry.get("id")
        if isinstance(carry_id, str):
            if carry_id in id_indices:
                diagnostics.append(f"{location}.id: duplicate carry id '{carry_id}'")
            else:
                id_indices[carry_id] = index
        order = carry.get("order")
        if isinstance(order, int) and not isinstance(order, bool) and order > 0:
            if order in orders:
                diagnostics.append(f"{location}.order: duplicate order {order}")
            if previous_order is not None and order <= previous_order:
                diagnostics.append(
                    f"{location}.order: must be greater than previous order {previous_order}"
                )
            orders.add(order)
            previous_order = order

    graph: dict[str, list[str]] = {}
    for index, carry in enumerate(carries):
        if not isinstance(carry, dict) or not isinstance(carry.get("id"), str):
            continue
        carry_id = carry["id"]
        dependencies = carry.get("depends_on")
        if not isinstance(dependencies, list):
            continue
        graph.setdefault(carry_id, [])
        seen: set[str] = set()
        for dep_index, dependency in enumerate(dependencies):
            location = f"carries[{index}].depends_on[{dep_index}]"
            if not isinstance(dependency, str):
                diagnostics.append(f"{location}: must be a string")
                continue
            if not KEBAB_RE.fullmatch(dependency):
                diagnostics.append(f"{location}: must be lowercase kebab-case")
            if dependency in seen:
                diagnostics.append(f"{location}: duplicate dependency '{dependency}'")
            seen.add(dependency)
            if dependency == carry_id:
                diagnostics.append(f"{location}: self-dependency '{dependency}'")
            elif dependency not in id_indices:
                diagnostics.append(f"{location}: unknown dependency '{dependency}'")
            elif id_indices[dependency] >= index:
                diagnostics.append(f"{location}: dependency '{dependency}' must appear earlier")
            if dependency in id_indices:
                graph[carry_id].append(dependency)

    state: dict[str, int] = {}
    stack: list[str] = []
    reported: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for dependency in graph.get(node, []):
            if state.get(dependency, 0) == 0:
                visit(dependency)
            elif state.get(dependency) == 1:
                start = stack.index(dependency)
                cycle = tuple(stack[start:] + [dependency])
                if cycle not in reported:
                    diagnostics.append(f"$: dependency cycle: {' -> '.join(cycle)}")
                    reported.add(cycle)
        stack.pop()
        state[node] = 2

    for carry_id in graph:
        if state.get(carry_id, 0) == 0:
            visit(carry_id)
    return diagnostics


def _diagnostic_sort_key(diagnostic: str) -> tuple[int, int, str]:
    match = re.match(r"carries\[(\d+)\]", diagnostic)
    if match:
        return (2, int(match.group(1)), diagnostic)
    if diagnostic.startswith("$:"):
        return (0, 0, diagnostic)
    return (1, 0, diagnostic)


def validate_manifest(manifest: Any, repo_root: Path = REPO_ROOT) -> list[str]:
    """Return deterministic diagnostics for a manifest."""
    diagnostics = _object_shape(manifest, "$", ROOT_REQUIRED)
    if not isinstance(manifest, dict):
        return diagnostics

    version = manifest.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        diagnostics.append("schema_version: must be integer 1")
    elif version != 1:
        diagnostics.append(f"schema_version: unsupported version {version}")

    carries = manifest.get("carries")
    if carries is not None and not isinstance(carries, list):
        diagnostics.append("carries: must be an array")
        return diagnostics
    if not isinstance(carries, list):
        return diagnostics

    for index, carry in enumerate(carries):
        location = f"carries[{index}]"
        diagnostics.extend(_object_shape(carry, location, CARRY_REQUIRED, CARRY_OPTIONAL))
        if isinstance(carry, dict):
            diagnostics.extend(_field_type_diagnostics(carry, location))
    diagnostics.extend(_identifier_and_dependency_diagnostics(carries))
    diagnostics.extend(_path_diagnostics(carries, repo_root))
    diagnostics.extend(_content_diagnostics(carries))
    diagnostics.extend(_check_diagnostics(carries))
    return sorted(diagnostics, key=_diagnostic_sort_key)


def build_status(manifest: Any, diagnostics: list[str] | None = None) -> dict[str, Any]:
    """Build a declaration-only status report."""
    errors = list(diagnostics or [])
    carries = manifest.get("carries", []) if isinstance(manifest, dict) else []
    if not isinstance(carries, list):
        carries = []
    rows: list[dict[str, Any]] = []
    for carry in carries:
        if not isinstance(carry, dict):
            continue
        checks = carry.get("checks")
        check_ids = (
            [check.get("id") for check in checks if isinstance(check, dict)]
            if isinstance(checks, list)
            else []
        )
        provenance = carry.get("provenance")
        rows.append(
            {
                "order": carry.get("order"),
                "id": carry.get("id"),
                "status": carry.get("status"),
                "dependencies": list(carry.get("depends_on", []))
                if isinstance(carry.get("depends_on"), list)
                else [],
                "provenance_count": len(provenance) if isinstance(provenance, list) else 0,
                "check_ids": check_ids,
            }
        )
    return {
        "schema_version": manifest.get("schema_version") if isinstance(manifest, dict) else None,
        "valid": not errors,
        "diagnostics": errors,
        "total": len(rows),
        "active": sum(row["status"] == "active" for row in rows),
        "retired": sum(row["status"] == "retired" for row in rows),
        "declared_checks": sum(len(row["check_ids"]) for row in rows),
        "carries": rows,
    }


def render_markdown(status: dict[str, Any]) -> str:
    """Render a status report as deterministic Markdown."""
    lines = [
        "# Fork Carry Manifest Status",
        "",
        f"- Schema version: `{status.get('schema_version')}`",
        f"- Valid: `{'yes' if status.get('valid') else 'no'}`",
        f"- Carries: `{status.get('total', 0)}` total, `{status.get('active', 0)}` active, "
        f"`{status.get('retired', 0)}` retired",
        f"- Declared checks: `{status.get('declared_checks', 0)}`",
        "",
        "| Order | ID | Status | Dependencies | Provenance | Checks |",
        "| ---: | --- | --- | --- | ---: | --- |",
    ]
    for row in status.get("carries", []):
        dependencies = ", ".join(row["dependencies"]) or "—"
        checks = ", ".join(row["check_ids"]) or "—"
        lines.append(
            f"| {row['order']} | `{row['id']}` | {row['status']} | {dependencies} | "
            f"{row['provenance_count']} | {checks} |"
        )
    diagnostics = status.get("diagnostics", [])
    if diagnostics:
        lines.extend(["", "## Diagnostics"])
        lines.extend(f"- {diagnostic}" for diagnostic in diagnostics)
    return "\n".join(lines) + "\n"


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = argparse.ArgumentParser(description="Read-only fork carry manifest tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "status"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
        subparser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"manifest unreadable or malformed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    diagnostics = validate_manifest(manifest, REPO_ROOT)
    valid = not diagnostics
    if args.command == "validate":
        report = {
            "schema_version": manifest.get("schema_version")
            if isinstance(manifest, dict)
            else None,
            "valid": valid,
            "diagnostics": diagnostics,
        }
        if args.json_output:
            sys.stdout.write(_json_text(report))
        elif valid:
            print("Fork carry manifest is valid.")
        else:
            for diagnostic in diagnostics:
                print(diagnostic, file=sys.stderr)
    else:
        status = build_status(manifest, diagnostics)
        sys.stdout.write(_json_text(status) if args.json_output else render_markdown(status))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
