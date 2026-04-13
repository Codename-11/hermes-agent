"""
MemPalace memory provider plugin for Hermes.

Local-first AI memory using a 4-layer stack (L0 identity, L1 essential story,
L2 on-demand, L3 deep search) with a temporal knowledge graph (SQLite) and
ChromaDB semantic search at ~/.mempalace/.

Design approved: 2026-04-09 by Bailey (Codename_11).
Build status: IN PROGRESS — core memory loop only (Phase 1 minimal).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ── Wing structure ────────────────────────────────────────────────────────────
WINGS = {
    "wing_victor": "Diary, observations, lessons learned from sessions",
    "wing_operator": "Bailey's preferences, corrections, patterns, personal facts",
    "system": "System docs — agents, hermes, infra, projects, operations (from Obsidian 3. System/)",
    "personal": "Personal knowledge — tech, finances, 3D printing, servers (from Obsidian 1. Personal/)",
    "business": "Business — Axiom-Labs, brands (from Obsidian 2. Business/)",
    "inbox": "Inbox — quick captures, drops, notes, unsorted (from Obsidian 0. Inbox/)",
}

LEGACY_WING_ALIASES = {
    "wing_system": "system",
    "wing_personal": "personal",
    "wing_business": "business",
    "wing_inbox": "inbox",
}

DEFAULT_WING = "wing_victor"


def normalize_wing_name(wing: Optional[str]) -> Optional[str]:
    """Map legacy Obsidian wing names to canonical MemPalace wings."""
    if wing is None:
        return None
    cleaned = str(wing).strip()
    if not cleaned:
        return None
    return LEGACY_WING_ALIASES.get(cleaned, cleaned)


def derive_kg_path(palace_path: str, kg_path: Optional[str] = None) -> str:
    """Resolve the KG sqlite path for a given palace path."""
    if kg_path and str(kg_path).strip():
        return str(Path(kg_path).expanduser())
    palace_dir = Path(palace_path).expanduser()
    return str(palace_dir.parent / "knowledge_graph.sqlite3")


def _query_matches_keyword(query: str, keyword: str) -> bool:
    """Match keywords conservatively to avoid false positives like me→memory."""
    if not query or not keyword:
        return False
    q = query.lower()
    kw = keyword.lower().strip()
    if not kw:
        return False
    if re.search(r"[\s\-/]", kw):
        return kw in q
    return re.search(rf"\b{re.escape(kw)}\b", q) is not None


def profile_identity_to_wing(agent_identity: str) -> str:
    """Return a stable per-profile wing name."""
    identity = (agent_identity or "").strip().lower()
    if identity == "victor":
        return "wing_victor"
    if identity == "mizu":
        return "wing_operator"
    slug = re.sub(r"[^a-z0-9]+", "_", identity).strip("_")
    return f"wing_{slug}" if slug else DEFAULT_WING

# ── KG entity extraction ──────────────────────────────────────────────────────
# Lightweight extraction patterns for on_pre_compress hook.
# Maps observed patterns → KG triples.
_ENTITY_RE = re.compile(
    r"(?i)\b(?:"
    r"(?P<subject_embedded>[\w ]{2,40})\s+"
    r"(?P<predicate>is|are|was|were|has|have|"
    r"likes?|loves?|hates?|prefers?|uses?|works on|works with|built|created|made|"
    r"owns?|runs?|hosts?|manages?|configured|set up|installed|deployed|"
    r"decided|chose|changed|updated|moved|renamed|deleted|fixed|broken|"
    r"connected to|linked to|paired with|installed as)\s+"
    r"(?P<object_embedded>[\w ]{2,60})"
    r")\b"
)

_SKIP_SUBJECTS = frozenset([
    "i", "me", "my", "we", "us", "our", "it", "its", "this", "that", "these", "those",
    "the", "a", "an", "what", "which", "who", "whom", "some", "any", "all", "each",
])

_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*([\"']?)([^\s\"']+)(\2)"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s+(is|was)\s+([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{12,}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{10,}"),
]

_SECRET_TERMS_RE = re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b")


def _redact_sensitive_text(text: str) -> str:
    """Redact obvious secrets before storing content durably."""
    if not text:
        return ""
    redacted = text
    redacted = _SENSITIVE_PATTERNS[0].sub(lambda m: f"{m.group(1)}=<redacted>", redacted)
    redacted = _SENSITIVE_PATTERNS[1].sub(lambda m: f"{m.group(1)} {m.group(2)} <redacted>", redacted)
    for pattern in _SENSITIVE_PATTERNS[2:]:
        redacted = pattern.sub("<redacted>", redacted)
    return redacted


def _extract_facts_from_text(text: str) -> List[Dict[str, str]]:
    """
    Lightweight fact extraction from text.
    Returns list of {subject, predicate, object} triples.
    """
    facts = []
    for match in _ENTITY_RE.finditer(text):
        subject = match.group("subject_embedded").strip()
        obj = match.group("object_embedded").strip()
        if len(subject) < 2 or len(obj) < 2:
            continue
        if subject.lower() in _SKIP_SUBJECTS:
            continue
        if _SECRET_TERMS_RE.search(subject) or _SECRET_TERMS_RE.search(obj):
            continue
        if obj == "<redacted>":
            continue
        # Basic dedup
        if subject.lower() == obj.lower():
            continue
        facts.append({
            "subject": subject,
            "predicate": match.group("predicate").lower(),
            "object": obj,
        })
    return facts


# ── Tool schemas ──────────────────────────────────────────────────────────────

SEARCH_SCHEMA = {
    "name": "mempalace_search",
    "description": "Search MemPalace long-term memory by semantic similarity. "
                   "Filters by wing and/or room for focused results.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "wing": {"type": "string", "description": "Limit search to a specific wing."},
            "room": {"type": "string", "description": "Limit search to a specific room."},
            "limit": {"type": "integer", "description": "Max results, 1-20 (default: 5).", "default": 5},
        },
        "required": ["query"],
    },
}

KG_QUERY_SCHEMA = {
    "name": "mempalace_kg_query",
    "description": "Query the MemPalace knowledge graph for all facts about an entity. "
                   "Supports temporal filtering — ask 'as of <date>' to see what was true at a given time.",
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Entity name to query."},
            "as_of": {"type": "string", "description": "Date string (YYYY-MM-DD) to query historical state."},
            "direction": {
                "type": "string",
                "description": "outgoing (entity→?), incoming (?→entity), or both.",
                "default": "both",
            },
        },
        "required": ["entity"],
    },
}

KG_TIMELINE_SCHEMA = {
    "name": "mempalace_kg_timeline",
    "description": "Get all knowledge graph facts in chronological order for an entity, "
                   "or global timeline if no entity specified.",
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Entity name (optional — omit for global timeline)."},
            "limit": {"type": "integer", "description": "Max facts to return, 1-200 (default: 50).", "default": 50},
        },
    },
}

STATUS_SCHEMA = {
    "name": "mempalace_status",
    "description": "Get MemPalace palace overview — total drawers, wing/room counts, "
                   "knowledge graph stats.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


# ── MemoryProvider ─────────────────────────────────────────────────────────────

class MempalaceMemoryProvider(MemoryProvider):
    """
    MemPalace memory provider for Hermes.

    Wings:
      wing_victor    — agent's own memory (diary, lessons, observations)
      wing_operator  — operator (Bailey) profile: preferences, corrections, patterns
      system         — server/infra state
      wing_projects — per-project rooms
      wing_hermes   — platform knowledge

    Sync: Herme's built-in memory runs alongside this. MemPalace is the deep store,
    built-in is the hot cache.
    """

    def __init__(self):
        self._hermes_home: str = ""
        self._session_id: str = ""
        self._agent_identity: str = ""
        self._platform: str = ""

        # MemoryStack (lazy init)
        self._stack = None          # type: Optional[Any]
        self._kg = None             # type: Optional[Any]
        self._stack_lock = threading.Lock()

        # Background threads
        self._sync_thread: Optional[threading.Thread] = None
        self._write_thread: Optional[threading.Thread] = None

        # Config
        self._palace_path: str = ""
        self._identity_path: str = ""
        self._kg_path: str = ""
        self._active: bool = False
        self._write_enabled: bool = True
        self._profile_wing: str = DEFAULT_WING  # this agent's primary wing

        # Tool schemas cached
        self._schemas: Optional[List[Dict[str, Any]]] = None

    @property
    def name(self) -> str:
        return "mempalace"

    # ── Availability ───────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """MemPalace is available if the pip package is installed."""
        try:
            __import__("mempalace")
            return True
        except Exception:
            return False

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """
        No required config — MemPalace is local-only at ~/.mempalace/.
        palace_path and identity_path can be overridden via env vars if needed.
        """
        return [
            {
                "key": "palace_path",
                "description": "Custom palace directory (default: ~/.mempalace/palace/)",
                "required": False,
            },
            {
                "key": "identity_path",
                "description": "Custom identity file (default: ~/.mempalace/identity.txt)",
                "required": False,
            },
            {
                "key": "kg_path",
                "description": "Custom knowledge-graph sqlite path (default: alongside palace_path)",
                "required": False,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Write non-secret config to ~/.hermes/mempalace.json."""
        config_path = Path(hermes_home) / "mempalace.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ── Init ───────────────────────────────────────────────────────────────────

    def initialize(self, session_id: str, **kwargs) -> None:
        from mempalace.config import MempalaceConfig
        from mempalace.layers import MemoryStack
        from mempalace.knowledge_graph import KnowledgeGraph

        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._platform = kwargs.get("platform", "cli")
        self._agent_identity = kwargs.get("agent_identity", "default")

        # Load config overrides if any
        config_path = Path(self._hermes_home) / "mempalace.json"
        overrides = {}
        if config_path.exists():
            try:
                overrides = json.loads(config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                overrides = {}

        cfg = MempalaceConfig()
        self._palace_path = overrides.get("palace_path") or os.environ.get(
            "MEMPALACE_PALACE_PATH", cfg.palace_path
        )
        self._identity_path = overrides.get("identity_path") or os.environ.get(
            "MEMPALACE_IDENTITY_PATH",
            str(Path(self._palace_path).expanduser().parent / "identity.txt")
        )
        self._kg_path = derive_kg_path(
            self._palace_path,
            overrides.get("kg_path") or os.environ.get("MEMPALACE_KG_PATH"),
        )

        # Profile-scoped wing — each Hermes profile gets its own primary wing.
        # Preserve explicit historical mappings for Victor/Mizu, but isolate any
        # other profile into its own dedicated wing.
        self._profile_wing = profile_identity_to_wing(self._agent_identity)

        # Skip writes for non-primary contexts (cron/flush subagents)
        agent_context = kwargs.get("agent_context", "")
        self._write_enabled = agent_context not in ("cron", "flush", "subagent")

        # Lazy-load MemoryStack and KG
        self._active = True
        self._schemas = [SEARCH_SCHEMA, KG_QUERY_SCHEMA, KG_TIMELINE_SCHEMA, STATUS_SCHEMA]

        logger.info(
            "[MemPalace] initialized session=%s identity=%s wing=%s write_enabled=%s palace=%s",
            session_id, self._agent_identity, self._profile_wing, self._write_enabled, self._palace_path,
        )

    def _get_stack(self):
        """Lazily create and cache the MemoryStack."""
        if self._stack is None:
            with self._stack_lock:
                if self._stack is None:
                    from mempalace.layers import MemoryStack
                    self._stack = MemoryStack(
                        palace_path=self._palace_path,
                        identity_path=self._identity_path,
                    )
        return self._stack

    def _get_kg(self):
        """Lazily create and cache the KnowledgeGraph."""
        if self._kg is None:
            with self._stack_lock:
                if self._kg is None:
                    from mempalace.knowledge_graph import KnowledgeGraph
                    self._kg = KnowledgeGraph(db_path=self._kg_path)
        return self._kg

    # ── System prompt ──────────────────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        if not self._active:
            return ""
        wings_desc = "\n".join(f"  - {k}: {v}" for k, v in WINGS.items())
        return (
            "# MemPalace Memory\n"
            f"Active. Palace: {self._palace_path}\n"
            f"Profile wing: {self._profile_wing}\n"
            "\n"
            "Available wings:\n"
            f"{wings_desc}\n"
            "\n"
            "Use mempalace_search, mempalace_kg_query, mempalace_kg_timeline, and mempalace_status "
            "to query and manage long-term memory.\n"
        )

    # ── Prefetch ───────────────────────────────────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """
        L2 on-demand retrieval based on query keywords.
        Attempts to detect which wing the query relates to and retrieves
        from that wing. Falls back to L3 global search if no wing detected.
        """
        if not self._active or not query.strip():
            return ""

        q = query.lower()

        # Wing detection from keywords
        wing_map = {
            "system": "system",
            "server": "system", "docker": "system", "service": "system",
            "infra": "system", "ubuntu": "system", "linux": "system",
            "hermes": "system", "agent": "system", "plugin": "system",
            "claude": "system", "project": "system",
            "personal": "personal", "tech": "personal", "3d print": "personal",
            "finance": "personal", "klipper": "personal",
            "business": "business", "axiom-labs": "business", "lumin": "business",
            "inbox": "inbox", "capture": "inbox", "quick": "inbox",
            "operator": "wing_operator", "bailey": "wing_operator", "preference": "wing_operator",
        }

        detected_wing = None
        for kw, wing in wing_map.items():
            if _query_matches_keyword(q, kw):
                detected_wing = wing
                break
        if detected_wing is None and (
            _query_matches_keyword(q, "me")
            or _query_matches_keyword(q, "my")
            or _query_matches_keyword(q, "myself")
            or _query_matches_keyword(q, "i")
        ):
            detected_wing = self._profile_wing

        try:
            stack = self._get_stack()
            if detected_wing:
                result = stack.recall(wing=detected_wing, n_results=8)
            else:
                result = stack.search(query, n_results=5)
            return result
        except Exception as e:
            logger.debug("[MemPalace] prefetch failed: %s", e)
            return ""

    # ── Turn sync ──────────────────────────────────────────────────────────────

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """
        After each turn: extract facts and write to KG + palace drawer.
        Non-blocking — spawns a background thread.
        """
        if not self._active or not self._write_enabled:
            return
        if not user_content.strip() or not assistant_content.strip():
            return

        combined = _redact_sensitive_text(f"{user_content}\n{assistant_content}")

        def _run():
            try:
                facts = _extract_facts_from_text(combined)
                kg = self._get_kg()
                wing = self._profile_wing
                for fact in facts[:10]:  # cap at 10 per turn
                    try:
                        kg.add_triple(
                            fact["subject"],
                            fact["predicate"],
                            fact["object"],
                            source_closet=f"turn:{session_id or 'unknown'}",
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("[MemPalace] sync_turn failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2.0)
        self._sync_thread = threading.Thread(target=_run, daemon=True, name="mempalace-sync")
        self._sync_thread.start()

    # ── Session end ───────────────────────────────────────────────────────────

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """
        At session boundary: ingest cleaned conversation into palace.
        Uses the conversation miner if available, otherwise writes a session summary.
        """
        if not self._active or not self._write_enabled or not messages:
            return

        # Clean messages
        cleaned = []
        for msg in messages:
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = str(msg.get("content", "") or "").strip()
            if content:
                cleaned.append({"role": role, "content": content})

        if len(cleaned) < 2:
            return

        def _run():
            try:
                # Write session summary drawer directly
                import chromadb
                from mempalace.palace import get_collection
                import hashlib

                col = get_collection(self._palace_path)
                session_id = self._session_id or "unknown"
                summary = self._summarize_messages(cleaned)
                drawer_id = f"drawer_{self._profile_wing}_session_{hashlib.sha256(session_id.encode()).hexdigest()[:16]}"

                col.upsert(
                    ids=[drawer_id],
                    documents=[summary],
                    metadatas=[{
                        "wing": self._profile_wing,
                        "room": "sessions",
                        "source_file": f"hermes://session/{session_id}",
                        "type": "session_summary",
                    }],
                )
                logger.info("[MemPalace] session drawer written: %s", drawer_id)
            except Exception as e:
                logger.debug("[MemPalace] on_session_end failed: %s", e)

        if self._write_thread and self._write_thread.is_alive():
            self._write_thread.join(timeout=2.0)
        self._write_thread = threading.Thread(target=_run, daemon=False, name="mempalace-session")
        self._write_thread.start()

    def _summarize_messages(self, messages: List[Dict[str, str]], max_chars: int = 1500) -> str:
        """Build a compact session summary."""
        lines = [f"## Session Summary ({len(messages)} turns)"]
        for msg in messages[-6:]:  # last 6 turns max
            role = msg.get("role", "?")
            content = _redact_sensitive_text(msg.get("content", ""))
            if len(content) > 300:
                content = content[:297] + "..."
            lines.append(f"[{role}]: {content}")
        summary = "\n".join(lines)
        if len(summary) > max_chars:
            summary = summary[:max_chars]
        return summary

    # ── Pre-compress ────────────────────────────────────────────────────────────

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """
        Extract facts from messages about to be compressed.
        Save to KG so nothing is lost when context is truncated.
        Side-effect only — returns no prompt text for reinjection.
        """
        if not self._active or not self._write_enabled or not messages:
            return ""

        combined = "\n".join(
            _redact_sensitive_text(str(msg.get("content", "") or "")) for msg in messages
            if msg.get("role") in ("user", "assistant")
        )

        facts = _extract_facts_from_text(combined)
        if not facts:
            return ""

        try:
            kg = self._get_kg()
            extracted = []
            for fact in facts[:15]:
                try:
                    kg.add_triple(
                        fact["subject"],
                        fact["predicate"],
                        fact["object"],
                        source_closet="pre_compress",
                    )
                    extracted.append(f"{fact['subject']} {fact['predicate']} {fact['object']}")
                except Exception:
                    pass
            if extracted:
                logger.debug("[MemPalace] on_pre_compress extracted %d fact(s)", len(extracted))
        except Exception as e:
            logger.debug("[MemPalace] on_pre_compress failed: %s", e)

        return ""

    # ── Memory write mirror ───────────────────────────────────────────────────

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """
        Mirror built-in memory tool writes to MemPalace drawers.
        action: 'add', 'replace', 'remove'
        target: 'memory' or 'user'
        """
        if not self._active or not self._write_enabled:
            return
        if action != "add" or not (content or "").strip():
            return

        def _run():
            try:
                import chromadb
                from mempalace.palace import get_collection
                import hashlib

                col = get_collection(self._palace_path)
                wing = "wing_operator" if target == "user" else self._profile_wing
                content_hash = hashlib.sha256(content.encode()).hexdigest()[:24]
                drawer_id = f"drawer_{wing}_memory_{content_hash}"

                col.upsert(
                    ids=[drawer_id],
                    documents=[_redact_sensitive_text(content.strip())],
                    metadatas=[{
                        "wing": wing,
                        "room": "memory_bank",
                        "source_file": f"hermes://memory/{target}",
                        "type": "explicit_memory",
                    }],
                )
            except Exception as e:
                logger.debug("[MemPalace] on_memory_write failed: %s", e)

        if self._write_thread and self._write_thread.is_alive():
            self._write_thread.join(timeout=2.0)
        self._write_thread = threading.Thread(target=_run, daemon=True, name="mempalace-memory-write")
        self._write_thread.start()

    # ── Tools ─────────────────────────────────────────────────────────────────

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if self._schemas is None:
            self._schemas = [SEARCH_SCHEMA, KG_QUERY_SCHEMA, KG_TIMELINE_SCHEMA, STATUS_SCHEMA]
        return self._schemas

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._active:
            return tool_error("MemPalace is not active")

        handler = "_tool_" + tool_name.replace("mempalace_", "")
        if hasattr(self, handler):
            return getattr(self, handler)(args)
        return tool_error(f"Unknown MemPalace tool: {tool_name}")

    def _tool_search(self, args: dict) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return tool_error("query is required")
        wing = normalize_wing_name(str(args.get("wing") or "").strip() or None)
        room = str(args.get("room") or "").strip() or None
        try:
            limit = max(1, min(20, int(args.get("limit", 5) or 5)))
        except Exception:
            limit = 5

        try:
            stack = self._get_stack()
            result = stack.search(query, wing=wing, room=room, n_results=limit)
            hits_raw = stack.l3.search_raw(query, wing=wing, room=room, n_results=limit)
            hits = []
            for h in hits_raw:
                hits.append({
                    "text": h.get("text", ""),
                    "wing": h.get("wing", ""),
                    "room": h.get("room", ""),
                    "source_file": h.get("source_file", ""),
                    "similarity": h.get("similarity"),
                })
            return json.dumps({"result": result, "hits": hits, "count": len(hits)})
        except Exception as exc:
            return tool_error(f"Search failed: {exc}")

    def _tool_kg_query(self, args: dict) -> str:
        entity = str(args.get("entity") or "").strip()
        if not entity:
            return tool_error("entity is required")
        as_of = str(args.get("as_of") or "").strip() or None
        direction = str(args.get("direction") or "both").strip() or "both"
        if direction not in ("outgoing", "incoming", "both"):
            direction = "both"

        try:
            kg = self._get_kg()
            results = kg.query_entity(entity, as_of=as_of, direction=direction)
            return json.dumps({"entity": entity, "facts": results, "count": len(results)})
        except Exception as exc:
            return tool_error(f"KG query failed: {exc}")

    def _tool_kg_timeline(self, args: dict) -> str:
        entity = str(args.get("entity") or "").strip() or None
        try:
            limit = max(1, min(200, int(args.get("limit", 50) or 50)))
        except Exception:
            limit = 50

        try:
            kg = self._get_kg()
            results = kg.timeline(entity_name=entity)
            results = results[:limit]
            return json.dumps({"entity": entity, "timeline": results, "count": len(results)})
        except Exception as exc:
            return tool_error(f"Timeline failed: {exc}")

    def _tool_status(self, args: dict) -> str:
        try:
            stack = self._get_stack()
            status = stack.status()
            kg = self._get_kg()
            kg_stats = kg.stats()
            status["kg"] = kg_stats
            return json.dumps(status)
        except Exception as exc:
            return tool_error(f"Status failed: {exc}")

    # ── Shutdown ───────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        for attr in ("_sync_thread", "_write_thread"):
            t = getattr(self, attr, None)
            if t and t.is_alive():
                t.join(timeout=5.0)
        if self._kg:
            try:
                self._kg.close()
            except Exception:
                pass
        self._active = False
        logger.info("[MemPalace] shutdown complete")


# ── Plugin registration ────────────────────────────────────────────────────────

def register(ctx):
    ctx.register_memory_provider(MempalaceMemoryProvider())
