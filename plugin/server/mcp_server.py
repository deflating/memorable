"""MCP server for Memorable.

Exposes tools to Claude Code for memory management:
- search_sessions: hybrid keyword + semantic search over sessions
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
from .embeddings import embed_text, cosine_distance


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
            "memorable_search_sessions": self._tool_search_sessions,
            "memorable_search_observations": self._tool_search_observations,
            "memorable_get_observations": self._tool_get_observations,
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

    def _tool_search_sessions(self, args: dict) -> str:
        """Hybrid search: keyword (SQL LIKE) + semantic (Apple NLEmbedding)."""
        query = args.get("query", "")
        limit = args.get("limit", 10)
        if not query:
            return "Please provide a search query."

        scored = []

        def _semantic_score(dist: float) -> float:
            """Normalize NLEmbedding distance to 0-1 similarity score.

            Distances typically range 0.8 (very similar) to 1.5 (unrelated).
            """
            return max(0.0, (1.5 - dist) / 0.7)

        # ── Embed query once ──
        query_emb = embed_text(query)

        # ── Keyword search (SQL LIKE) — keyword matches get a bonus ──
        keyword_results = self.db.search_sessions(query, limit=limit * 2)
        seen_ids = set()
        for s in keyword_results:
            seen_ids.add(s["id"])
            text = f"{s['title']}. {s.get('summary', '')} {s.get('header', '')}"
            if query_emb:
                text_emb = embed_text(text)
                if text_emb:
                    from .embeddings import cosine_distance_vectors
                    dist = cosine_distance_vectors(query_emb, text_emb)
                    sem = _semantic_score(dist)
                else:
                    sem = 0.0
            else:
                dist = cosine_distance(query, text)
                sem = _semantic_score(dist)
            score = 0.6 * sem + 0.4
            scored.append((score, s))

        # ── Semantic-only pass on all sessions ──
        all_sessions = self.db.get_all_session_texts(limit=500)
        for s in all_sessions:
            if s["id"] in seen_ids:
                continue
            text = f"{s['title']}. {s.get('summary', '')} {s.get('header', '')}"
            if query_emb:
                text_emb = embed_text(text)
                if text_emb:
                    from .embeddings import cosine_distance_vectors
                    dist = cosine_distance_vectors(query_emb, text_emb)
                    sem = _semantic_score(dist)
                else:
                    # NULL embedding — skip this session
                    continue
            else:
                dist = cosine_distance(query, text)
                sem = _semantic_score(dist)
            if sem > 0.15:
                scored.append((sem, s))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = scored[:limit]

        if not results:
            return f"No sessions found matching '{query}'."

        lines = [f"Found {len(results)} session(s) matching '{query}':\n"]
        for score, s in results:
            lines.append(f"### {s['title']} ({s['date']}) [score: {score:.2f}]")
            lines.append(f"Messages: {s.get('message_count', 0)} | Words: {s.get('word_count', 0)}")
            if s.get("header"):
                lines.append(s["header"])
            if s.get("summary"):
                lines.append(s["summary"])
            # Include compressed_50 (Haiku summary) if available
            if s.get("compressed_50"):
                lines.append(f"\n{s['compressed_50'][:300]}")
            lines.append("")

        return "\n".join(lines)

    def _tool_search_observations(self, args: dict) -> str:
        """Hybrid search across observations AND user prompts."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        obs_type = args.get("type")

        if not query:
            return "Please provide a search query."

        scored = []

        def _semantic_score(dist: float) -> float:
            return max(0.0, (1.5 - dist) / 0.7)

        # ── Embed query once ──
        query_emb = embed_text(query)

        # ── Observations: keyword + semantic ──
        if not obs_type or obs_type != "prompt":
            keyword_results = self.db.search_observations_keyword(query, limit=limit * 2)

            for obs in keyword_results:
                text = f"{obs['title']}. {obs['summary']}"
                if query_emb:
                    text_emb = embed_text(text)
                    if text_emb:
                        from .embeddings import cosine_distance_vectors
                        dist = cosine_distance_vectors(query_emb, text_emb)
                        sem = _semantic_score(dist)
                    else:
                        sem = 0.0
                else:
                    dist = cosine_distance(query, text)
                    sem = _semantic_score(dist)
                score = 0.6 * sem + 0.4
                scored.append((score, "obs", obs))

            # Semantic-only pass on recent observations
            all_obs = self.db.get_all_observation_texts(limit=500)
            seen_ids = {obs["id"] for obs in keyword_results}
            for obs in all_obs:
                if obs["id"] in seen_ids:
                    continue
                text = f"{obs['title']}. {obs['summary']}"
                if query_emb:
                    text_emb = embed_text(text)
                    if text_emb:
                        from .embeddings import cosine_distance_vectors
                        dist = cosine_distance_vectors(query_emb, text_emb)
                        sem = _semantic_score(dist)
                    else:
                        # NULL embedding — skip
                        continue
                else:
                    dist = cosine_distance(query, text)
                    sem = _semantic_score(dist)
                if sem > 0.15:
                    scored.append((sem, "obs", obs))

        # ── User prompts: keyword + semantic ──
        if not obs_type or obs_type == "prompt":
            keyword_prompts = self.db.search_user_prompts(query, limit=limit * 2)

            for p in keyword_prompts:
                if query_emb:
                    text_emb = embed_text(p["prompt_text"][:500])
                    if text_emb:
                        from .embeddings import cosine_distance_vectors
                        dist = cosine_distance_vectors(query_emb, text_emb)
                        sem = _semantic_score(dist)
                    else:
                        sem = 0.0
                else:
                    dist = cosine_distance(query, p["prompt_text"][:500])
                    sem = _semantic_score(dist)
                score = 0.6 * sem + 0.4
                scored.append((score, "prompt", p))

        # Filter observations by type (but not prompts — they don't have types)
        if obs_type and obs_type != "prompt":
            scored = [(s, k, o) for s, k, o in scored
                      if k == "prompt" or o.get("observation_type") == obs_type]

        scored.sort(key=lambda x: x[0], reverse=True)
        results = scored[:limit]

        if not results:
            return f"No results found matching '{query}'."

        lines = [f"Found {len(results)} result(s) matching '{query}':\n"]
        for score, kind, item in results:
            if kind == "prompt":
                preview = item["prompt_text"][:150]
                if len(item["prompt_text"]) > 150:
                    preview += "..."
                lines.append(
                    f"\U0001f4ac **User prompt** "
                    f"(session: {item['session_id'][:12]}..., score: {score:.2f})"
                )
                lines.append(f"  \"{preview}\"")
            else:
                type_emoji = {
                    "bugfix": "\U0001f534", "feature": "\U0001f7e3",
                    "refactor": "\U0001f504", "change": "\u2705",
                    "discovery": "\U0001f535", "decision": "\u2696\ufe0f",
                    "session_summary": "\U0001f4cb",
                }.get(item.get("observation_type", ""), "\u2022")

                lines.append(
                    f"#{item['id']} {type_emoji} **{item['title']}** "
                    f"({item.get('observation_type', '?')}, score: {score:.2f})"
                )
                lines.append(f"  {item['summary']}")
                if item.get("files") and item["files"] != "[]":
                    lines.append(f"  Files: {item['files']}")
            lines.append("")

        return "\n".join(lines)

    def _tool_get_observations(self, args: dict) -> str:
        """Get observations for a specific session or recent observations."""
        session_id = args.get("session_id")
        limit = args.get("limit", 50)

        if session_id:
            results = self.db.get_observations_by_session(session_id, limit=limit)
            if not results:
                return f"No observations found for session '{session_id}'."
            header = f"Observations for session {session_id}:"
        else:
            results = self.db.get_recent_observations(limit=limit)
            if not results:
                return "No observations recorded yet."
            header = f"Recent observations (last {limit}):"

        lines = [header, ""]
        for obs in results:
            type_emoji = {
                "bugfix": "\U0001f534", "feature": "\U0001f7e3",
                "refactor": "\U0001f504", "change": "\u2705",
                "discovery": "\U0001f535", "decision": "\u2696\ufe0f",
                "session_summary": "\U0001f4cb",
            }.get(obs.get("observation_type", ""), "\u2022")

            lines.append(
                f"#{obs['id']} {type_emoji} **{obs['title']}** ({obs.get('observation_type', '?')})"
            )
            lines.append(f"  {obs['summary']}")
            if obs.get("files") and obs["files"] != "[]":
                lines.append(f"  Files: {obs['files']}")
            lines.append("")

        return "\n".join(lines)

    def _tool_record_significant(self, args: dict) -> str:
        description = args.get("description", "")
        entity_name = args.get("entity", "")
        entity_type = args.get("type", "moment")

        if not description:
            return "Please provide a description of the significant moment."

        name = entity_name or description[:80]
        self.db.add_entity(
            name=name,
            entity_type=entity_type,
            description=description,
        )

        return f"Recorded: {name} (type: {entity_type})"

    def _tool_query_kg(self, args: dict) -> str:
        entity = args.get("entity")
        entity_type = args.get("type")
        limit = args.get("limit", 30)

        results = self.db.query_kg(
            entity=entity,
            entity_type=entity_type,
            limit=limit,
        )

        if not results:
            return "No knowledge graph entries found."

        lines = [f"Found {len(results)} KG entries:\n"]
        for r in results:
            meta = ""
            if r.get("rel_type"):
                meta = f" --[{r['rel_type']}]--> {r.get('target_name', '?')}"
            lines.append(
                f"- **{r['name']}** ({r['type']}): {r.get('description', '')}{meta}"
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
            f"- Observations: {stats.get('observations', 0)}",
            f"- Pending observations: {stats.get('pending_observations', 0)}",
            f"- User prompts captured: {stats.get('user_prompts', 0)}",
            f"- Pending transcripts: {stats['pending_transcripts']}",
            f"\n### Config",
            f"- Processing: Haiku via claude -p (summaries) + GLiNER (metadata) + Apple FM (headers)",
            f"- Summary model: {config_info.get('summary_model', 'haiku')}",
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
        "name": "memorable_search_sessions",
        "description": "Search past sessions using hybrid keyword + semantic search (Apple NLEmbedding). Finds sessions by exact keyword match AND conceptual similarity. Use for questions like 'when did we discuss X' or 'what happened with Y'.",
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
        "name": "memorable_search_observations",
        "description": "Search observations and user prompts across sessions. Uses hybrid search: semantic similarity (Apple NLEmbedding) + keyword matching. Searches both tool-use observations and what the user said. Use for 'when did I say X', 'what happened with Y', or 'when did we decide Z'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to find in observations and user prompts",
                },
                "type": {
                    "type": "string",
                    "description": "Filter by type. Use 'prompt' to search only user messages.",
                    "enum": ["bugfix", "feature", "refactor", "change", "discovery", "decision", "session_summary", "prompt"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 20)",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "memorable_get_observations",
        "description": "Get observations for a specific session or recent observations. Each observation captures what happened during tool usage: type (bugfix/feature/refactor/change/discovery/decision), title, summary, and files involved.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to get observations for. If omitted, returns recent observations across all sessions.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 50)",
                    "default": 50,
                },
            },
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
            },
            "required": ["description"],
        },
    },
    {
        "name": "memorable_query_kg",
        "description": "Query the knowledge graph for entities and relationships. Search by entity name or type.",
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
