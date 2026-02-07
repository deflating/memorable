"""MCP server for Memorable.

Exposes tools to Claude Code for memory management:
- get_startup_seed: recency-gradient context for session startup
- search_sessions: keyword search over compressed transcripts
- record_significant: flag important moments mid-conversation
- query_kg: structured knowledge graph queries
- get_system_status: processing queue, KG stats, health
"""

import json
import sys
import time
from pathlib import Path

from .db import MemorableDB
from .config import Config


class MemorableMCP:
    """Handles MCP JSON-RPC protocol over stdio."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.db = MemorableDB(
            Path(self.config["db_path"]),
            sync_url=self.config.get("sync_url", ""),
            auth_token=self.config.get("sync_auth_token", ""),
        )

    def run(self):
        """Main loop: read JSON-RPC from stdin, write responses to stdout."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self._write_error(-1, -32700, "Parse error")
                continue

            req_id = request.get("id")
            method = request.get("method", "")
            params = request.get("params", {})

            try:
                result = self._dispatch(method, params)
                self._write_result(req_id, result)
            except Exception as e:
                self._write_error(req_id, -32603, str(e))

    def _dispatch(self, method: str, params: dict):
        handlers = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_list_tools,
            "tools/call": self._handle_call_tool,
            "notifications/initialized": lambda p: None,
        }
        handler = handlers.get(method)
        if handler is None:
            raise ValueError(f"Unknown method: {method}")
        return handler(params)

    # ── Protocol Handlers ─────────────────────────────────────

    def _handle_initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "memorable",
                "version": "0.2.0",
            }
        }

    def _handle_list_tools(self, params: dict) -> dict:
        return {"tools": TOOLS}

    def _handle_call_tool(self, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments", {})

        tool_handlers = {
            "memorable_get_startup_seed": self._tool_get_startup_seed,
            "memorable_search_sessions": self._tool_search_sessions,
            "memorable_record_significant": self._tool_record_significant,
            "memorable_query_kg": self._tool_query_kg,
            "memorable_get_system_status": self._tool_get_system_status,
        }

        handler = tool_handlers.get(name)
        if handler is None:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                "isError": True,
            }

        try:
            result = handler(args)
            return {"content": [{"type": "text", "text": result}]}
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            }

    # ── Tool Implementations ──────────────────────────────────

    def _tool_get_startup_seed(self, args: dict) -> str:
        """Build startup context:
        - Sacred facts (always)
        - record_significant entries (always)
        - Recent session notes (Apple model summaries with emoji headers)
        """
        parts = []

        # Sacred facts (priority 10) — always included
        sacred = self.db.get_sacred_facts()
        if sacred:
            parts.append("## Sacred Facts")
            for fact in sacred:
                parts.append(f"- **{fact['name']}** ({fact['type']}): {fact['description']}")

        # Recent record_significant entries (high-priority KG entities)
        significant = self.db.query_kg(min_priority=7, limit=20)
        if significant:
            # Filter to non-sacred entries (sacred already shown above)
            sig_entries = [e for e in significant if e.get("priority", 0) < 10]
            if sig_entries:
                parts.append("\n## Important (recorded)")
                for e in sig_entries:
                    parts.append(f"- **{e['name']}** (p:{e['priority']}): {e.get('description', '')}")

        # Recent session summaries
        seed_count = self.config.get("seed_session_count", 10)
        recent = self.db.get_recent_summaries(limit=seed_count)
        if recent:
            parts.append("\n## Recent Sessions")
            for s in recent:
                header = s.get("header", "")
                summary = s.get("summary", "")
                parts.append(f"\n### {s['title']} ({s['date']}, {s['word_count']}w)")
                if header:
                    parts.append(header)
                if summary:
                    parts.append(summary)

        if not parts:
            return "No memory data yet. This is a fresh Memorable installation."

        return "\n".join(parts)

    def _tool_search_sessions(self, args: dict) -> str:
        query = args.get("query", "")
        limit = args.get("limit", 10)
        if not query:
            return "Please provide a search query."

        results = self.db.search_sessions(query, limit=limit)
        if not results:
            return f"No sessions found matching '{query}'."

        lines = [f"Found {len(results)} session(s) matching '{query}':\n"]
        for s in results:
            lines.append(f"### {s['title']} ({s['date']})")
            lines.append(f"Messages: {s['message_count']} | Words: {s['word_count']}")
            if s.get("header"):
                lines.append(s["header"])
            if s.get("summary"):
                lines.append(s["summary"])
            else:
                # Fall back to compressed transcript preview
                preview = s["compressed_50"][:800]
                if len(s["compressed_50"]) > 800:
                    preview += "..."
                lines.append(preview)
            lines.append("")

        return "\n".join(lines)

    def _tool_record_significant(self, args: dict) -> str:
        description = args.get("description", "")
        entity_name = args.get("entity", "")
        entity_type = args.get("type", "moment")
        priority = args.get("priority", 7)

        if not description:
            return "Please provide a description of the significant moment."

        name = entity_name or description[:80]
        self.db.add_entity(
            name=name,
            entity_type=entity_type,
            description=description,
            priority=min(max(priority, 1), 10),
        )

        return f"Recorded: {name} (priority {priority}, type: {entity_type})"

    def _tool_query_kg(self, args: dict) -> str:
        entity = args.get("entity")
        entity_type = args.get("type")
        min_priority = args.get("min_priority", 0)
        limit = args.get("limit", 30)

        results = self.db.query_kg(
            entity=entity,
            entity_type=entity_type,
            min_priority=min_priority,
            limit=limit,
        )

        if not results:
            return "No knowledge graph entries found."

        lines = [f"Found {len(results)} KG entries:\n"]
        for r in results:
            meta = ""
            if r.get("rel_type"):
                meta = f" --[{r['rel_type']}]--> {r.get('target_name', '?')}"
            priority_marker = "" if r.get("priority", 5) < 10 else " [SACRED]"
            lines.append(
                f"- **{r['name']}** ({r['type']}, p:{r.get('priority', '?')}){priority_marker}"
                f": {r.get('description', '')}{meta}"
            )

        return "\n".join(lines)

    def _tool_get_system_status(self, args: dict) -> str:
        stats = self.db.get_stats()
        config_info = self.config.as_dict()

        lines = [
            "## Memorable System Status\n",
            f"- Sessions stored: {stats['sessions']}",
            f"- Total words processed: {stats['total_words_processed']:,}",
            f"- KG entities: {stats['kg_entities']}",
            f"- KG relationships: {stats['kg_relationships']}",
            f"- Sacred facts (p10): {stats['sacred_facts']}",
            f"- Context seeds: {stats['context_seeds']}",
            f"- Pending transcripts: {stats['pending_transcripts']}",
            f"\n### Config",
            f"- Processing: LLMLingua-2 (compression) + Apple Foundation Model (summaries)",
            f"- Storage compression: {config_info.get('compression_rate_storage')}",
            f"- Seed: last {config_info.get('seed_session_count', 10)} session notes",
            f"- Watcher enabled: {config_info.get('watcher_enabled')}",
        ]

        return "\n".join(lines)

    # ── IO ────────────────────────────────────────────────────

    def _write_result(self, req_id, result):
        if result is None:
            return  # notifications don't get responses
        response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        self._write(response)

    def _write_error(self, req_id, code, message):
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
        self._write(response)

    def _write(self, obj):
        line = json.dumps(obj)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# ── Tool Definitions ──────────────────────────────────────────

TOOLS = [
    {
        "name": "memorable_get_startup_seed",
        "description": "Get startup context for the current session. Uses a recency gradient: sacred facts, flagged important moments, last 2-3 sessions at moderate compression, and older session skeletons. Call this at the start of every session.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memorable_search_sessions",
        "description": "Search past sessions by keyword. Searches across compressed transcripts and titles. Use for questions like 'when did we discuss X' or 'what happened with Y'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to find in session transcripts and titles",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "memorable_record_significant",
        "description": "Record a significant moment, decision, or fact to the knowledge graph during conversation. Use when something important happens that should be remembered long-term.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Description of the significant moment or fact",
                },
                "entity": {
                    "type": "string",
                    "description": "Short name for the entity (defaults to truncated description)",
                },
                "type": {
                    "type": "string",
                    "description": "Entity type: person, project, decision, moment, concept, location",
                    "enum": ["person", "project", "decision", "moment", "concept", "location"],
                    "default": "moment",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority 1-10. 10=sacred/immutable, 7-9=important, 4-6=contextual, 1-3=ephemeral",
                    "default": 7,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "memorable_query_kg",
        "description": "Query the knowledge graph for entities and relationships. Search by entity name, type, or minimum priority level.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity name to search for (partial match)",
                },
                "type": {
                    "type": "string",
                    "description": "Filter by entity type",
                    "enum": ["person", "project", "decision", "moment", "concept", "location"],
                },
                "min_priority": {
                    "type": "integer",
                    "description": "Minimum priority level to return (0-10)",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 30)",
                    "default": 30,
                },
            },
        },
    },
    {
        "name": "memorable_get_system_status",
        "description": "Get Memorable system status: session count, KG stats, pending transcripts, configuration.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]
