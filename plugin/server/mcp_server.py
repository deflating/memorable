"""MCP server for Memorable.

Exposes tools to Claude Code for memory management:
- memorable_recall: ask the memory oracle about past context
- memorable_search: substring search across sessions, observations, prompts
- memorable_get_status: file counts and health
- memorable_onboard: first-time setup
- memorable_update_seed: update seed files
"""

import json
import logging
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"


def _get_user_name() -> str:
    """Read user_name from config."""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("user_name", "")
    except Exception:
        pass
    return ""


def _valid_seed_files() -> set[str]:
    """Return the set of valid seed file names (without .md)."""
    user_name = _get_user_name()
    valid = {"claude", "now"}
    if user_name:
        valid.add(user_name)
    return valid


class MemorableMCP:
    """Handles MCP JSON-RPC protocol over stdio."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self._oracle = None  # Lazy-loaded on first recall call

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
                "version": "4.0.0",
            }
        }

    def _handle_list_tools(self, params: dict) -> dict:
        return {"tools": TOOLS}

    def _handle_call_tool(self, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments", {})

        tool_handlers = {
            "memorable_recall": self._tool_recall,
            "memorable_search": self._tool_search,
            "memorable_get_status": self._tool_get_status,
            "memorable_onboard": self._tool_onboard,
            "memorable_update_seed": self._tool_update_seed,
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

    def _tool_recall(self, args: dict) -> str:
        """Ask the memory oracle about past context."""
        question = args.get("question", "").strip()
        if not question:
            return "Please provide a question."

        if self._oracle is None:
            try:
                from oracle.oracle_client import OracleClient
                self._oracle = OracleClient()
            except ImportError:
                return "Error: oracle module not available."

        try:
            return self._oracle.ask(question)
        except ConnectionError:
            return "Oracle is unreachable. The MLX server on the Mac Mini may not be running."
        except Exception as e:
            logger.error("Oracle recall failed: %s", e)
            return f"Oracle error: {e}"

    def _tool_search(self, args: dict) -> str:
        """Unified search across sessions, observations, prompts."""
        query = args.get("query", "").strip()
        if not query:
            return "Please provide a search query."

        filter_type = args.get("type")
        days_back = args.get("days_back", 30)
        limit = args.get("limit", 20)

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        query_lower = query.lower()
        results = []

        if not filter_type or filter_type == "session":
            results.extend(self._search_sessions(query_lower, cutoff))

        if not filter_type or filter_type == "observation":
            results.extend(self._search_jsonl_dir("observations", query_lower, cutoff))

        if not filter_type or filter_type == "prompt":
            results.extend(self._search_jsonl_dir("prompts", query_lower, cutoff))

        if not filter_type or filter_type == "note":
            results.extend(self._search_jsonl_dir("notes", query_lower, cutoff))

        # Sort by timestamp descending, deduplicate
        results.sort(key=lambda r: r.get("ts", ""), reverse=True)
        seen = set()
        deduped = []
        for r in results:
            key = (r.get("ts", ""), r.get("text", "")[:100])
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        results = deduped[:limit]

        if not results:
            return f"No results found matching '{query}' in the last {days_back} days."

        lines = [f"Found {len(results)} result(s) matching '{query}':\n"]
        for r in results:
            source = r.get("source", "?")
            ts = r.get("ts", "")[:16]
            text = r.get("text", "")
            if len(text) > 200:
                text = text[:200] + "..."
            lines.append(f"- [{source}] ({ts}) {text}")

        return "\n".join(lines)

    def _tool_get_status(self, args: dict) -> str:
        """System status: count files and lines."""
        seed_count = len(list(SEEDS_DIR.glob("*.md"))) if SEEDS_DIR.exists() else 0

        session_dir = DATA_DIR / "sessions"
        session_count = len(list(session_dir.glob("*.json"))) if session_dir.exists() else 0
        obs_count = self._count_jsonl_dir("observations")
        prompt_count = self._count_jsonl_dir("prompts")
        notes_count = self._count_jsonl_dir("notes")

        lines = [
            "## Memorable System Status\n",
            f"- Seeds: {seed_count} (identity files)",
            f"- Notes: {notes_count} (session-end LLM notes)",
            f"- Sessions: {session_count}",
            f"- Observations: {obs_count}",
            f"- Prompts: {prompt_count}",
            f"- Data dir: {DATA_DIR}",
            f"- Architecture: v4 files-first (Syncthing sync)",
        ]
        return "\n".join(lines)

    # ── File Search Helpers ───────────────────────────────────

    def _search_jsonl_dir(self, subdir: str, query_lower: str, cutoff: datetime) -> list[dict]:
        """Search all JSONL files in a sharded subdirectory."""
        d = DATA_DIR / subdir
        if not d.exists():
            return []
        source_type = subdir.rstrip("s")  # anchors -> anchor
        results = []
        for jsonl_file in d.glob("*.jsonl"):
            try:
                with open(jsonl_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = entry.get("ts", "")
                        if not self._after_cutoff(ts, cutoff):
                            continue
                        text = self._entry_text(entry, source_type)
                        if query_lower in text.lower():
                            results.append({"source": source_type, "ts": self._normalize_ts(ts), "text": text})
            except OSError:
                continue
        return results

    def _search_sessions(self, query_lower: str, cutoff: datetime) -> list[dict]:
        """Search session JSON files."""
        session_dir = DATA_DIR / "sessions"
        if not session_dir.exists():
            return []
        results = []
        for f in session_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            date_str = data.get("date", "")
            if not self._after_cutoff(date_str, cutoff):
                continue
            title = data.get("title", "")
            summary = data.get("summary", "")
            if query_lower in f"{title} {summary}".lower():
                text = title
                if summary:
                    text += f" — {summary}"
                results.append({"source": "session", "ts": self._normalize_ts(date_str), "text": text})
        return results

    def _entry_text(self, entry: dict, source_type: str) -> str:
        """Extract display text from a JSONL entry based on its type."""
        if source_type == "observation":
            tool = entry.get("tool", "")
            summary = entry.get("summary", "")
            file_ = entry.get("file", "")
            text = f"[{tool}] {summary}" if tool else summary
            if file_:
                text += f" ({file_})"
            return text
        if source_type == "prompt":
            return entry.get("prompt", "")
        if source_type == "note":
            note = entry.get("note", "")
            # Return just the first line (Summary section) for search results
            for line in note.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    return line[:200]
            return note[:200]
        return entry.get("note", entry.get("summary", entry.get("prompt", json.dumps(entry))))

    def _normalize_ts(self, ts) -> str:
        """Convert any timestamp format to ISO string for consistent sorting."""
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return str(ts)

    def _after_cutoff(self, ts, cutoff: datetime) -> bool:
        """Check if a timestamp is after the cutoff. Handles ISO strings and epoch floats."""
        if not ts:
            return False
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                ts_clean = str(ts).replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_clean)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except (ValueError, TypeError, OSError):
            try:
                return str(ts)[:10] >= cutoff.strftime("%Y-%m-%d")
            except Exception:
                return True

    def _count_jsonl_dir(self, subdir: str) -> int:
        """Count lines across all JSONL files in a sharded subdirectory."""
        d = DATA_DIR / subdir
        if not d.exists():
            return 0
        count = 0
        for jsonl_file in d.glob("*.jsonl"):
            try:
                with open(jsonl_file) as f:
                    count += sum(1 for line in f if line.strip())
            except OSError:
                continue
        return count

    # ── Seed Tools ─────────────────────────────────────────────

    def _tool_onboard(self, args: dict) -> str:
        """Set up Memorable with user name. Creates {name}.md seed file."""
        SEEDS_DIR.mkdir(parents=True, exist_ok=True)

        name = args.get("name", "").strip().lower()
        about = args.get("about", "").strip()

        if not name:
            return "Error: 'name' is required. This becomes your seed file name ({name}.md)."

        # Save user_name to config
        config = {}
        try:
            if CONFIG_PATH.exists():
                config = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
        config["user_name"] = name
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")

        # Create user seed file
        user_file = SEEDS_DIR / f"{name}.md"
        if about:
            content = about if about.startswith("#") else f"# {name.capitalize()}\n\n{about}\n"
            user_file.write_text(content)
        elif not user_file.exists():
            user_file.write_text(f"# {name.capitalize()}\n")

        # Create claude.md if it doesn't exist
        claude_file = SEEDS_DIR / "claude.md"
        if not claude_file.exists():
            claude_file.write_text("# Claude\n")

        return f"Onboarding complete. Seed files: {name}.md + claude.md"

    def _tool_update_seed(self, args: dict) -> str:
        """Update a seed file. Pass 'user' to update the user's file, or 'claude' for claude.md."""
        file_name = args.get("file", "").strip().lower()
        content = args.get("content", "").strip()

        valid = _valid_seed_files()

        # Accept "user" as an alias for the user's actual name
        if file_name == "user":
            user_name = _get_user_name()
            if user_name:
                file_name = user_name
            else:
                return "Error: no user_name configured. Run memorable_onboard first."

        if not file_name:
            return f"Error: 'file' is required. Valid options: {', '.join(sorted(valid))} (or 'user')"
        if file_name not in valid:
            return f"Error: unknown seed file '{file_name}'. Valid options: {', '.join(sorted(valid))} (or 'user')"
        if not content:
            return "Error: 'content' is required."

        SEEDS_DIR.mkdir(parents=True, exist_ok=True)

        # Preserve the header if content doesn't start with one
        title = file_name.capitalize()
        if not content.startswith("#"):
            content = f"# {title}\n\n{content}\n"

        seed_path = SEEDS_DIR / f"{file_name}.md"
        if seed_path.exists():
            shutil.copy2(seed_path, seed_path.with_suffix(".md.bak"))
        seed_path.write_text(content)
        return f"Updated {file_name}.md seed file."

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
        "name": "memorable_recall",
        "description": "Ask the memory oracle about past sessions, decisions, people, projects, or anything from Matt's history. The oracle holds all session notes and knowledge graph in context. Use for: 'what do you know about X', 'when did we discuss Y', 'what was decided about Z'. NOTE: First call after context changes may take a few minutes to warm up.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Your question about past context, sessions, people, or decisions",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "memorable_search",
        "description": "Search across all memory: sessions, notes (session-end summaries), observations (tool usage), and user prompts. Use for 'when did we discuss X', 'what happened with Y', or 'when did I say Z'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (case-insensitive substring match)",
                },
                "type": {
                    "type": "string",
                    "description": "Filter to a specific source type",
                    "enum": ["session", "note", "observation", "prompt"],
                },
                "days_back": {
                    "type": "integer",
                    "description": "How far back to search (default 30 days)",
                    "default": 30,
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
        "name": "memorable_get_status",
        "description": "Get Memorable system status: counts of seeds, sessions, observations, and prompts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memorable_onboard",
        "description": "Set up Memorable with user's name. Creates {name}.md + claude.md seed files. The user's file contains everything about them; claude.md contains how to be with them.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "User's first name (becomes the seed filename, e.g. 'matt' -> matt.md)",
                },
                "about": {
                    "type": "string",
                    "description": "Initial content for the user's seed file (markdown)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "memorable_update_seed",
        "description": "Update a seed file. Three files: the user's file (pass 'user' or their name) for identity/people/projects, 'claude' for behavioral instructions, and 'now' for current state snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Which seed file to update: 'user' (or the user's name), 'claude', or 'now'",
                },
                "content": {
                    "type": "string",
                    "description": "New content for the seed file (markdown)",
                },
            },
            "required": ["file", "content"],
        },
    },
]
