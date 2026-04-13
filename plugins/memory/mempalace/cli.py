"""CLI commands for MemPalace memory provider.

Handles: hermes mempalace status | sync | prune | mine
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from plugins.memory.mempalace import derive_kg_path


# PARA directory → wing mapping (same as cron script)
PARA_MAP = {
    "0. Inbox": "inbox",
    "1. Personal": "personal",
    "2. Business": "business",
    "3. System": "system",
}


def _runtime_settings(hermes_home: Optional[str] = None) -> dict:
    """Resolve runtime paths from the same config/env model as the provider."""
    from mempalace.config import MempalaceConfig

    try:
        from hermes_constants import get_hermes_home
        resolved_home = Path(hermes_home) if hermes_home else Path(get_hermes_home())
    except Exception:
        resolved_home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"

    overrides = {}
    cfg_path = resolved_home / "mempalace.json"
    if cfg_path.exists():
        try:
            overrides = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            overrides = {}

    cfg = MempalaceConfig()
    palace_path = overrides.get("palace_path") or os.environ.get("MEMPALACE_PALACE_PATH") or cfg.palace_path
    identity_path = overrides.get("identity_path") or os.environ.get(
        "MEMPALACE_IDENTITY_PATH",
        str(Path(palace_path).expanduser().parent / "identity.txt"),
    )
    kg_path = derive_kg_path(
        palace_path,
        overrides.get("kg_path") or os.environ.get("MEMPALACE_KG_PATH"),
    )
    vault_path = (
        overrides.get("vault_path")
        or os.environ.get("MEMPALACE_VAULT_PATH")
        or os.path.expanduser("~/obsidian-vault")
    )

    try:
        from hermes_cli.profiles import get_active_profile_name
        agent_identity = get_active_profile_name() or "victor"
    except Exception:
        agent_identity = os.environ.get("HERMES_PROFILE") or "victor"

    return {
        "hermes_home": str(resolved_home),
        "palace_path": str(Path(palace_path).expanduser()),
        "identity_path": str(Path(identity_path).expanduser()),
        "kg_path": str(Path(kg_path).expanduser()),
        "vault_path": str(Path(vault_path).expanduser()),
        "python_executable": sys.executable,
        "agent_identity": agent_identity,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_collection(palace_path: str):
    """Get the ChromaDB collection."""
    from mempalace.palace import get_collection
    return get_collection(palace_path)


def _get_all_source_files(col) -> List[Tuple[str, str]]:
    """Return list of (drawer_id, source_file) for all drawers with a source_file."""
    all_data = col.get(include=["metadatas"])
    results = []
    for i, meta in enumerate(all_data["metadatas"]):
        src = meta.get("source_file", "")
        if src and not src.startswith("hermes://"):
            results.append((all_data["ids"][i], src))
    return results


def _find_orphans(col, vault_path: str) -> List[Tuple[str, str]]:
    """Find drawers whose source_file no longer exists on disk.

    Source files in ChromaDB are stored as absolute paths (resolved through
    symlinks, e.g. /mnt/obsidian-vault/Drive/-Vault-/Axiom-Vault/...).
    We check existence directly since the path is already absolute and valid.
    """
    all_sources = _get_all_source_files(col)
    orphans = []
    for drawer_id, src_file in all_sources:
        if os.path.isabs(src_file):
            if not os.path.exists(src_file):
                orphans.append((drawer_id, src_file))
        else:
            found = False
            vault_real = os.path.realpath(vault_path)
            for base in [vault_real, vault_path] + [os.path.join(vault_path, d) for d in PARA_MAP]:
                candidate = os.path.join(base, src_file)
                if os.path.exists(candidate):
                    found = True
                    break
            if not found:
                orphans.append((drawer_id, src_file))
    return orphans


# ── Commands ──────────────────────────────────────────────────────────────────

def _cmd_status(args) -> None:
    """Show palace status — drawer counts, wing breakdown, KG stats."""
    try:
        from mempalace.layers import MemoryStack
        from mempalace.knowledge_graph import KnowledgeGraph

        settings = _runtime_settings()
        stack = MemoryStack(
            palace_path=settings["palace_path"],
            identity_path=settings["identity_path"],
        )
        status = stack.status()

        print("\n📊 MemPalace Status")
        print(f"   Palace: {settings['palace_path']}")
        print(f"   Total drawers: {status.get('total_drawers', '?')}")

        l0 = status.get("L0_identity", {})
        if l0.get("exists"):
            print(f"   L0 identity: ✅ ({l0.get('tokens', '?')} tokens)")
        else:
            print(f"   L0 identity: ❌ missing ({settings['identity_path']})")

        col = _get_collection(settings["palace_path"])
        all_data = col.get(include=["metadatas"])
        wing_counts = {}
        for meta in all_data["metadatas"]:
            wing = meta.get("wing", "unknown")
            wing_counts[wing] = wing_counts.get(wing, 0) + 1

        if wing_counts:
            print("\n   Wings:")
            for wing, count in sorted(wing_counts.items(), key=lambda x: -x[1]):
                print(f"     {wing}: {count} drawers")

        try:
            kg = KnowledgeGraph(db_path=settings["kg_path"])
            kg_stats = kg.stats()
            print(f"\n   Knowledge Graph:")
            print(f"     Entities: {kg_stats.get('entities', 0)}")
            print(f"     Triples: {kg_stats.get('current_facts', 0)} current, {kg_stats.get('expired_facts', 0)} expired")
        except Exception as kg_err:
            print(f"\n   Knowledge Graph: ⚠ {kg_err}")

        orphans = _find_orphans(col, settings["vault_path"])
        if orphans:
            print(f"\n   ⚠️  {len(orphans)} orphaned drawers (source files deleted)")
        else:
            print(f"\n   ✅ No orphaned drawers")

        print()

    except Exception as e:
        print(f"❌ Status failed: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_sync(args) -> None:
    """Re-mine Obsidian vault with PARA mapping, optionally prune orphans."""
    import subprocess

    settings = _runtime_settings()
    vault = args.vault or settings["vault_path"]
    if not os.path.isdir(vault):
        print(f"❌ Vault not found: {vault}", file=sys.stderr)
        sys.exit(1)

    print(f"🔄 Syncing vault: {vault}")
    had_error = False

    for dirname, wing in PARA_MAP.items():
        para_path = os.path.join(vault, dirname)
        if not os.path.isdir(para_path):
            print(f"   ⚠ Skipping {dirname} — not found")
            continue

        if not os.path.exists(os.path.join(para_path, "mempalace.yaml")):
            print(f"   Initializing {dirname}...")
            init_result = subprocess.run(
                [settings["python_executable"], "-m", "mempalace", "init", "--yes", para_path],
                capture_output=True, text=True,
            )
            if init_result.returncode != 0:
                had_error = True
                detail = init_result.stderr.strip() or init_result.stdout.strip() or "mempalace init failed"
                print(f"      ⚠ {detail}")
                continue

        print(f"   📁 Mining {dirname} → {wing}")
        result = subprocess.run(
            [
                settings["python_executable"],
                "-m",
                "mempalace",
                "mine",
                para_path,
                "--wing",
                wing,
                "--agent",
                settings["agent_identity"],
            ],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                print(f"      {line}")
        if result.returncode != 0:
            had_error = True
            detail = result.stderr.strip() or result.stdout.strip() or "mempalace mine failed"
            print(f"      ⚠ {detail}")

    if args.prune:
        _cmd_prune(args)

    if had_error:
        print("❌ Sync completed with errors", file=sys.stderr)
        sys.exit(1)

    print("✅ Sync complete\n")


def _cmd_prune(args) -> None:
    """Remove orphaned drawers (source files that no longer exist)."""
    try:
        settings = _runtime_settings()
        vault_path = getattr(args, "vault", None) or settings["vault_path"]
        col = _get_collection(settings["palace_path"])
        orphans = _find_orphans(col, vault_path)

        if not orphans:
            print("✅ No orphaned drawers found")
            return

        if args.dry_run:
            print(f"\n🔍 Found {len(orphans)} orphaned drawers (dry run — not deleting):")
            for drawer_id, src in orphans[:20]:
                print(f"   {src}")
            if len(orphans) > 20:
                print(f"   ... and {len(orphans) - 20} more")
            return

        print(f"\n🧹 Pruning {len(orphans)} orphaned drawers...")
        orphan_ids = [o[0] for o in orphans]

        deleted = 0
        for i in range(0, len(orphan_ids), 500):
            batch = orphan_ids[i:i + 500]
            col.delete(ids=batch)
            deleted += len(batch)
            if len(orphan_ids) > 500:
                print(f"   Deleted {deleted}/{len(orphan_ids)}...")

        print(f"✅ Pruned {deleted} orphaned drawers\n")

    except Exception as e:
        print(f"❌ Prune failed: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_mine(args) -> None:
    """Mine a specific directory into the palace."""
    import subprocess

    settings = _runtime_settings()
    target = args.path
    if not os.path.isdir(target):
        print(f"❌ Directory not found: {target}", file=sys.stderr)
        sys.exit(1)

    wing = args.wing or "system"

    if not os.path.exists(os.path.join(target, "mempalace.yaml")):
        print(f"   Initializing {target}...")
        init_result = subprocess.run(
            [settings["python_executable"], "-m", "mempalace", "init", "--yes", target],
            capture_output=True, text=True,
        )
        if init_result.returncode != 0:
            detail = init_result.stderr.strip() or init_result.stdout.strip() or "mempalace init failed"
            print(f"⚠ {detail}", file=sys.stderr)
            sys.exit(1)

    print(f"⛏️  Mining {target} → {wing}")
    result = subprocess.run(
        [
            settings["python_executable"],
            "-m",
            "mempalace",
            "mine",
            target,
            "--wing",
            wing,
            "--agent",
            settings["agent_identity"],
        ],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "mempalace mine failed"
        print(f"⚠ {detail}", file=sys.stderr)
        sys.exit(1)
    print("✅ Mining complete\n")


# ── CLI Registration ─────────────────────────────────────────────────────────

def register_cli(subparser) -> None:
    """Build the ``hermes mempalace`` argparse subcommand tree.

    Called by the plugin CLI registration system during argparse setup.
    The *subparser* is the parser for ``hermes mempalace``.
    """
    subparser.set_defaults(func=mempalace_command)
    subs = subparser.add_subparsers(dest="mempalace_command")

    subs.add_parser(
        "status",
        help="Show palace status — drawer counts, wings, KG stats, orphans",
    )

    sync_parser = subs.add_parser(
        "sync",
        help="Re-mine Obsidian vault into PARA-mapped wings",
    )
    sync_parser.add_argument(
        "--vault", metavar="PATH", default=None,
        help="Vault path (default: configured vault path or ~/obsidian-vault)",
    )
    sync_parser.add_argument(
        "--prune", action="store_true",
        help="Also prune orphaned drawers after mining",
    )
    sync_parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Show what would be pruned without deleting (with --prune)",
    )

    prune_parser = subs.add_parser(
        "prune",
        help="Remove orphaned drawers (source files that no longer exist)",
    )
    prune_parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Show what would be pruned without deleting",
    )

    mine_parser = subs.add_parser(
        "mine",
        help="Mine a specific directory into the palace",
    )
    mine_parser.add_argument(
        "path", metavar="PATH",
        help="Directory to mine",
    )
    mine_parser.add_argument(
        "--wing", metavar="WING", default=None,
        help="Wing to mine into (default: system)",
    )


def mempalace_command(args) -> None:
    """Dispatch ``hermes mempalace <subcommand>``."""
    cmd = getattr(args, "mempalace_command", None)
    if cmd == "status":
        _cmd_status(args)
    elif cmd == "sync":
        _cmd_sync(args)
    elif cmd == "prune":
        _cmd_prune(args)
    elif cmd == "mine":
        _cmd_mine(args)
    else:
        print("Usage: hermes mempalace {status|sync|prune|mine}")
        print("  status  — Palace overview, wing counts, orphan check")
        print("  sync    — Re-mine Obsidian vault (--prune to clean orphans)")
        print("  prune   — Remove orphaned drawers (--dry-run to preview)")
        print("  mine    — Mine a specific directory into the palace")
