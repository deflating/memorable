"""MCP server for Memorable.

Exposes tools to Claude Code for memory management:
- search_sessions: keyword search over session index
- search_observations: search observations and prompts
- get_system_status: file counts and health
"""

import json
import sys
from pathlib import Path

from .db import MemorableDB, DEFAULT_INDEX_PATH, DATA_DIR
from .config import Config


class MemorableMCP:
    """Handles MCP JSON-RPC protocol over stdio."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.db = MemorableDB(DEFAULT_INDEX_PATH)
        # Rebuild index if stale
        self.db.rebuild_if_needed()

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
                "version": "0.3.0",
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
        """Keyword search across session index."""
        query = args.get("query", "")
        limit = args.get("limit", 10)
        if not query:
            return "Please provide a search query."

        # Rebuild index if needed before searching
        self.db.rebuild_if_needed()

        results = self.db.search_sessions(query, limit=limit)

        if not results:
            return f"No sessions found matching '{query}'."

        lines = [f"Found {len(results)} session(s) matching '{query}':\n"]
        for s in results:
            lines.append(f"### {s['title']} ({s['date']})")
            lines.append(f"Messages: {s.get('message_count', 0)} | Words: {s.get('word_count', 0)}")
            if s.get("summary"):
                lines.append(s["summary"])
            lines.append("")

        return "\n".join(lines)

    def _tool_search_observations(self, args: dict) -> str:
        """Search across observations and user prompts."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        search_type = args.get("type")

        if not query:
            return "Please provide a search query."

        self.db.rebuild_if_needed()

        lines = []

        # Search observations
        if not search_type or search_type != "prompt":
            obs_results = self.db.search_observations(query, limit=limit)
            for obs in obs_results:
                lines.append(f"- [{obs.get('tool', '')}] {obs.get('summary', '')} ({obs.get('ts', '')[:10]})")
                if obs.get("file"):
                    lines.append(f"  File: {obs['file']}")

        # Search prompts
        if not search_type or search_type == "prompt":
            prompt_results = self.db.search_prompts(query, limit=limit)
            for p in prompt_results:
                preview = p.get("prompt", "")[:150]
                if len(p.get("prompt", "")) > 150:
                    preview += "..."
                lines.append(f"- [prompt] \"{preview}\" ({p.get('ts', '')[:10]})")

        if not lines:
            return f"No results found matching '{query}'."

        return f"Found results matching '{query}':\n\n" + "\n".join(lines)

    def _tool_get_system_status(self, args: dict) -> str:
        session_count = MemorableDB.get_session_file_count()

        # Count observations and prompts from files
        obs_count = 0
        obs_file = DATA_DIR / "observations.jsonl"
        if obs_file.exists():
            with open(obs_file) as f:
                obs_count = sum(1 for line in f if line.strip())

        prompt_count = 0
        prompts_file = DATA_DIR / "prompts.jsonl"
        if prompts_file.exists():
            with open(prompts_file) as f:
                prompt_count = sum(1 for line in f if line.strip())

        lines = [
            "## Memorable System Status\n",
            f"- Sessions: {session_count} (JSON files)",
            f"- Observations: {obs_count} (JSONL)",
            f"- User prompts: {prompt_count} (JSONL)",
            f"- Architecture: files-first (Syncthing sync)",
            f"- Data dir: ~/.memorable/data/",
            f"\n### Processing",
            f"- Summary model: {self.config.get('summary_model', 'haiku')}",
            f"- Watcher enabled: {self.config.get('watcher_enabled')}",
        ]

        return "\n".join(lines)

    # ── IO ────────────────────────────────────────────────────

    def _write_result(self, req_id, result):
        if result is None:
            return
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
        "description": "Search past sessions by keyword. Finds sessions where the query appears in the title or summary. Use for questions like 'when did we discuss X' or 'what happened with Y'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to find in session titles and summaries",
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
        "description": "Search observations (tool usage) and user prompts across sessions. Use for 'when did I say X', 'what happened with Y', or 'when did we use tool Z'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to find in observations and user prompts",
                },
                "type": {
                    "type": "string",
                    "description": "Filter: 'prompt' to search only user messages.",
                    "enum": ["prompt"],
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
        "name": "memorable_get_system_status",
        "description": "Get Memorable system status: session count, observation count, configuration.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]
