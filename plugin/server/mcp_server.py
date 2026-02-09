"""MCP server for Memorable.

Exposes tools to Claude Code for memory management:
- memorable_search: unified search across anchors, sessions, observations, prompts
- memorable_get_status: file counts and health
- memorable_write_anchor: mid-session memory checkpoint
- memorable_onboard: first-time setup
- memorable_update_seed: update seed files
"""

import json
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
VALID_SEED_FILES = {"identity", "preferences", "people", "projects"}


class MemorableMCP:
    """Handles MCP JSON-RPC protocol over stdio."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()

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
            "memorable_search": self._tool_search,
            "memorable_get_status": self._tool_get_status,
            "memorable_write_anchor": self._tool_write_anchor,
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

    def _tool_search(self, args: dict) -> str:
        """Unified search across anchors, sessions, observations, prompts."""
        query = args.get("query", "").strip()
        if not query:
            return "Please provide a search query."

        filter_type = args.get("type")
        days_back = args.get("days_back", 30)
        limit = args.get("limit", 20)

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        query_lower = query.lower()
        results = []

        if not filter_type or filter_type == "anchor":
            results.extend(self._search_jsonl_dir("anchors", query_lower, cutoff))

        if not filter_type or filter_type == "session":
            results.extend(self._search_sessions(query_lower, cutoff))

        if not filter_type or filter_type == "observation":
            results.extend(self._search_jsonl_dir("observations", query_lower, cutoff))
            results.extend(self._search_flat_jsonl("observations.jsonl", "observation", query_lower, cutoff))

        if not filter_type or filter_type == "prompt":
            results.extend(self._search_jsonl_dir("prompts", query_lower, cutoff))
            results.extend(self._search_flat_jsonl("prompts.jsonl", "prompt", query_lower, cutoff))

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

        anchor_count = self._count_jsonl_dir("anchors")
        session_dir = DATA_DIR / "sessions"
        session_count = len(list(session_dir.glob("*.json"))) if session_dir.exists() else 0
        obs_count = self._count_jsonl_dir("observations") + self._count_flat_jsonl("observations.jsonl")
        prompt_count = self._count_jsonl_dir("prompts") + self._count_flat_jsonl("prompts.jsonl")

        lines = [
            "## Memorable System Status\n",
            f"- Seeds: {seed_count} (identity files)",
            f"- Anchors: {anchor_count}",
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

    def _search_flat_jsonl(self, filename: str, source_type: str, query_lower: str, cutoff: datetime) -> list[dict]:
        """Search a flat JSONL file at the data root (legacy format)."""
        filepath = DATA_DIR / filename
        if not filepath.exists():
            return []
        results = []
        try:
            with open(filepath) as f:
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
            pass
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
        if source_type == "anchor":
            parts = []
            if entry.get("summary"):
                parts.append(entry["summary"])
            if entry.get("decisions"):
                parts.append(f"Decisions: {', '.join(entry['decisions'])}")
            if entry.get("open_threads"):
                parts.append(f"Threads: {', '.join(entry['open_threads'])}")
            return " | ".join(parts) if parts else json.dumps(entry)
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
        return entry.get("summary", entry.get("prompt", json.dumps(entry)))

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

    def _count_flat_jsonl(self, filename: str) -> int:
        """Count lines in a flat JSONL file at the data root."""
        filepath = DATA_DIR / filename
        if not filepath.exists():
            return 0
        try:
            with open(filepath) as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    # ── Anchor & Seed Tools ───────────────────────────────────

    def _tool_write_anchor(self, args: dict) -> str:
        """Write an anchor — a mid-session memory checkpoint."""
        summary = args.get("summary", "").strip()
        if not summary:
            return "Error: summary is required."

        mood = args.get("mood", "")
        decisions = args.get("decisions", [])
        open_threads = args.get("open_threads", [])
        session_id = args.get("session_id", "unknown")

        # Get machine_id
        machine_id = self.config.get("machine_id")
        if not machine_id:
            machine_id = socket.gethostname()

        anchors_dir = DATA_DIR / "anchors"
        anchors_dir.mkdir(parents=True, exist_ok=True)
        anchor_file = anchors_dir / f"{machine_id}.jsonl"

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session_id,
            "summary": summary,
            "machine": machine_id,
        }
        if mood:
            entry["mood"] = mood
        if decisions:
            entry["decisions"] = decisions
        if open_threads:
            entry["open_threads"] = open_threads

        with open(anchor_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        return "Anchor saved."

    # ── Seed Tools ─────────────────────────────────────────────

    def _tool_onboard(self, args: dict) -> str:
        """Populate seed files from onboarding info."""
        SEEDS_DIR.mkdir(parents=True, exist_ok=True)

        name = args.get("name", "").strip()
        identity = args.get("identity", "").strip()
        preferences = args.get("preferences", "").strip()
        people = args.get("people", "").strip()
        projects = args.get("projects", "").strip()

        updated = []

        if name or identity:
            content = "# Identity\n\n"
            if name:
                content += f"**Name:** {name}\n\n"
            if identity:
                content += f"{identity}\n"
            (SEEDS_DIR / "identity.md").write_text(content)
            updated.append("identity")

        if preferences:
            content = f"# Preferences\n\n{preferences}\n"
            (SEEDS_DIR / "preferences.md").write_text(content)
            updated.append("preferences")

        if people:
            content = f"# People\n\n{people}\n"
            (SEEDS_DIR / "people.md").write_text(content)
            updated.append("people")

        if projects:
            content = f"# Projects\n\n{projects}\n"
            (SEEDS_DIR / "projects.md").write_text(content)
            updated.append("projects")

        if updated:
            return f"Onboarding complete. Updated seed files: {', '.join(updated)}"
        return "No information provided. Pass at least name or identity to get started."

    def _tool_update_seed(self, args: dict) -> str:
        """Update a specific seed file."""
        file_name = args.get("file", "").strip().lower()
        content = args.get("content", "").strip()

        if not file_name:
            return f"Error: 'file' is required. Valid options: {', '.join(sorted(VALID_SEED_FILES))}"
        if file_name not in VALID_SEED_FILES:
            return f"Error: unknown seed file '{file_name}'. Valid options: {', '.join(sorted(VALID_SEED_FILES))}"
        if not content:
            return "Error: 'content' is required."

        SEEDS_DIR.mkdir(parents=True, exist_ok=True)

        # Preserve the header if content doesn't start with one
        title = file_name.capitalize()
        if not content.startswith("#"):
            content = f"# {title}\n\n{content}\n"

        (SEEDS_DIR / f"{file_name}.md").write_text(content)
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
        "name": "memorable_search",
        "description": "Search across all memory: anchors (session summaries with decisions/threads), sessions, observations (tool usage), and user prompts. Use for 'when did we discuss X', 'what happened with Y', or 'when did I say Z'.",
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
                    "enum": ["anchor", "session", "observation", "prompt"],
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
        "description": "Get Memorable system status: counts of seeds, anchors, sessions, observations, and prompts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memorable_write_anchor",
        "description": "Write an anchor — a mid-session memory checkpoint. Call this when prompted by the [Memorable] anchor reminder. Summarize what's happened since the last anchor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of conversation since last anchor point",
                },
                "mood": {
                    "type": "string",
                    "description": "Current emotional register (e.g., 'focused', 'frustrated', 'playful')",
                },
                "decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key decisions made since last anchor",
                },
                "open_threads": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Things left unfinished or still in progress",
                },
            },
            "required": ["summary"],
        },
    },
    {
        "name": "memorable_onboard",
        "description": "Set up Memorable with user identity and preferences. Populates seed files that are injected at every session start. Call this during first-time setup or when the user wants to update their profile.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "User's name",
                },
                "identity": {
                    "type": "string",
                    "description": "Free text about who the user is — background, personality, important facts",
                },
                "preferences": {
                    "type": "string",
                    "description": "How the user wants Claude to behave — tone, style, anti-patterns",
                },
                "people": {
                    "type": "string",
                    "description": "Important people in the user's life — names, relationships, context",
                },
                "projects": {
                    "type": "string",
                    "description": "Active projects and their status",
                },
            },
        },
    },
    {
        "name": "memorable_update_seed",
        "description": "Update a specific seed file (identity, preferences, people, or projects). Use this to modify one section without affecting others.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Which seed file to update",
                    "enum": ["identity", "preferences", "people", "projects"],
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
