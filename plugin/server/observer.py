"""Apple-native observation pipeline for Memorable.

Uses Apple Foundation Model (afm) for observation generation and
Apple NaturalLanguage framework for sentence embeddings. Runs
entirely on-device via the Neural Engine — zero API cost.

If quality is insufficient, swap _call_apple_model and embed_text
implementations. The interface stays the same.
"""

import json
import subprocess
from pathlib import Path

from .db import MemorableDB
from .config import Config
from .embeddings import embed_text, cosine_distance  # noqa: F401 — re-exported


# ── Apple Foundation Model ────────────────────────────────────

def _call_afm(prompt: str) -> str:
    """Call Apple Foundation Model via afm CLI."""
    env = {**__import__("os").environ, "TOKENIZERS_PARALLELISM": "false"}
    try:
        result = subprocess.run(
            ["afm", "-s", prompt],
            capture_output=True, text=True, timeout=30, env=env,
        )
        output = result.stdout.strip()
        if "Context window exceeded" in output or "Context window exceeded" in (result.stderr or ""):
            return ""
        return output
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


# ── Observation Generation ────────────────────────────────────

SKIP_TOOLS = {
    "TodoWrite", "AskUserQuestion", "ListMcpResourcesTool",
    "ToolSearch", "EnterPlanMode", "ExitPlanMode",
}

# Map our simple action types to the stored observation_type
_ACTION_TO_TYPE = {
    "read": "discovery",
    "search": "discovery",
    "edit": "change",
    "write": "change",
    "run": "change",
    "delete": "change",
}


# Infer action from tool name — don't ask afm to classify
_TOOL_ACTION = {
    "Read": "read", "Grep": "search", "Glob": "search",
    "Edit": "edit", "Write": "write", "Bash": "run",
    "WebFetch": "read", "WebSearch": "search",
    "NotebookEdit": "edit",
}

# Keywords in conversation context that indicate observation intent
_TYPE_SIGNALS = {
    "bugfix": {"fix", "bug", "broke", "broken", "crash", "error", "issue",
               "wrong", "fail", "failing", "debug", "patch", "regression"},
    "feature": {"add", "new", "implement", "create", "build", "feature",
                "introduce", "support"},
    "refactor": {"refactor", "clean", "rename", "move", "restructure",
                 "simplify", "extract", "reorganize", "deduplicate"},
    "decision": {"decide", "decision", "chose", "choose", "pick", "option",
                 "approach", "strategy", "should we", "let's go with"},
}

# Lazy-loaded GLiNER for entity extraction from observations
_gliner_model = None


def _get_gliner():
    global _gliner_model
    if _gliner_model is None:
        try:
            from gliner import GLiNER
            _gliner_model = GLiNER.from_pretrained("urchade/gliner_small-v2.1")
        except ImportError:
            pass
    return _gliner_model


def _classify_observation_type(action: str, context_before: str,
                                context_after: str, tool_output: str) -> str:
    """Infer a richer observation type from conversation context.

    Uses keyword signals from the assistant message (context_before) and
    user message (context_after) to distinguish bugfix/feature/refactor/decision
    from generic change/discovery.
    """
    context = f"{context_before} {context_after}".lower()

    # Check for specific intent signals, strongest match wins
    best_type = None
    best_count = 0
    for obs_type, keywords in _TYPE_SIGNALS.items():
        count = sum(1 for kw in keywords if kw in context)
        if count > best_count:
            best_count = count
            best_type = obs_type

    if best_type and best_count >= 2:
        return best_type

    # Fall back to action-based type
    return _ACTION_TO_TYPE.get(action, "change")


def _extract_entities_gliner(text: str) -> list[str]:
    """Extract named entities from text using GLiNER. Returns entity names."""
    model = _get_gliner()
    if model is None:
        return []
    try:
        labels = ["person", "technology", "software tool", "project"]
        results = model.predict_entities(text[:500], labels, threshold=0.4)
        skip = {"user", "claude", "assistant", "human", "option", "options"}
        seen = set()
        entities = []
        for ent in results:
            name = ent["text"].strip()
            if name.lower() not in skip and len(name) > 1 and name.lower() not in seen:
                seen.add(name.lower())
                entities.append(name)
        return entities[:5]
    except Exception:
        return []


def generate_observation(tool_data: dict) -> dict | None:
    """Generate a structured observation from a queued tool event.

    Combines mechanical descriptions from tool metadata with:
    - Context-aware type classification (bugfix/feature/refactor/decision)
    - GLiNER entity extraction for richer summaries
    - Conversation context for the WHY, not just the WHAT
    """
    tool_name = tool_data.get("tool_name", "")
    if tool_name in SKIP_TOOLS:
        return None

    tool_input_raw = tool_data.get("tool_input", "")
    tool_output_raw = tool_data.get("tool_response", "")
    context_before = tool_data.get("context_before", "")
    context_after = tool_data.get("context_after", "")

    # Skip trivial outputs
    if len(tool_output_raw.strip()) < 20:
        return None

    action = _TOOL_ACTION.get(tool_name, "run")
    files = _extract_files(tool_input_raw)

    title, summary = _describe_tool_call(tool_name, tool_input_raw, tool_output_raw, files)

    # Classify with context awareness
    obs_type = _classify_observation_type(action, context_before, context_after, tool_output_raw)

    # Extract entities from tool output to enrich the summary
    entity_text = tool_output_raw[:500] if tool_name in ("Grep", "Read", "WebSearch") else ""
    if context_before:
        entity_text = f"{context_before[:200]} {entity_text}"
    entities = _extract_entities_gliner(entity_text) if entity_text else []

    # Build enriched summary: mechanical description + context + entities
    parts = [summary]
    if context_after and len(context_after.strip()) > 10:
        # Add a brief note about what the user was asking for
        user_intent = context_after.strip().split('\n')[0][:120]
        parts.append(f"Context: {user_intent}")
    if entities:
        parts.append(f"Entities: {', '.join(entities)}")
    enriched_summary = " | ".join(parts)

    return {
        "type": obs_type,
        "action": action,
        "title": title[:80],
        "summary": enriched_summary[:500],
        "files": files,
    }


def _extract_files(tool_input: str) -> list[str]:
    """Pull file paths from tool input JSON."""
    files = []
    try:
        data = json.loads(tool_input) if isinstance(tool_input, str) else tool_input
        for key in ("file_path", "path", "notebook_path"):
            if key in data and data[key]:
                files.append(data[key])
    except (json.JSONDecodeError, TypeError):
        pass
    return files


def _describe_tool_call(tool_name: str, tool_input_raw: str, tool_output_raw: str, files: list[str]) -> tuple[str, str]:
    """Build a concise, informative description from tool data.

    Returns (title, summary) — no LLM needed.
    """
    data = {}
    try:
        data = json.loads(tool_input_raw) if isinstance(tool_input_raw, str) else (tool_input_raw or {})
    except (json.JSONDecodeError, TypeError):
        pass

    def short_path(p):
        """Last 2 path components: 'server/observer.py'"""
        parts = p.rstrip("/").split("/")
        return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]

    file_str = short_path(files[0]) if files else ""

    if tool_name == "Read":
        title = f"Read {file_str}" if file_str else "Read a file"
        # Check if it was a partial read
        offset = data.get("offset")
        limit = data.get("limit")
        if offset or limit:
            parts = []
            if offset: parts.append(f"from line {offset}")
            if limit: parts.append(f"{limit} lines")
            title += f" ({', '.join(parts)})"
        return title, title

    elif tool_name == "Edit":
        old = data.get("old_string", "")
        new = data.get("new_string", "")
        replace_all = data.get("replace_all", False)
        if file_str:
            if replace_all and old:
                title = f"Replaced all '{_snip(old, 30)}' in {file_str}"
            elif old and new:
                # Try to describe the nature of the edit
                title = f"Edited {file_str}"
                summary = f"Changed '{_snip(old, 60)}' to '{_snip(new, 60)}' in {file_str}"
                return title, summary
            else:
                title = f"Edited {file_str}"
        else:
            title = "Edited a file"
        return title, title

    elif tool_name == "Write":
        title = f"Wrote {file_str}" if file_str else "Wrote a file"
        content = data.get("content", "")
        if content:
            lines = content.count("\n") + 1
            title += f" ({lines} lines)"
        return title, title

    elif tool_name == "Grep":
        pattern = data.get("pattern", "")
        path = data.get("path", "")
        path_str = short_path(path) if path else "codebase"
        matches = _count_matches(tool_output_raw)
        title = f"Searched for '{_snip(pattern, 30)}'"
        if matches is not None:
            title += f" ({matches} match{'es' if matches != 1 else ''})"
        summary = f"Searched {path_str} for '{_snip(pattern, 50)}'"
        if matches is not None:
            summary += f" — {matches} match{'es' if matches != 1 else ''}"
        return title, summary

    elif tool_name == "Glob":
        pattern = data.get("pattern", "")
        matches = _count_matches(tool_output_raw)
        title = f"Found files matching '{_snip(pattern, 30)}'"
        if matches is not None:
            title += f" ({matches} file{'s' if matches != 1 else ''})"
        return title, title

    elif tool_name == "Bash":
        cmd = data.get("command", "")
        desc = data.get("description", "")
        if desc:
            # Claude provides descriptions for commands — use them
            title = desc
            summary = f"$ {_snip(cmd, 80)}" if cmd else desc
            return _snip(title, 80), summary
        elif cmd:
            # Extract the primary command
            primary = _extract_primary_command(cmd)
            title = f"Ran: {_snip(primary, 60)}"
            return title, f"$ {_snip(cmd, 200)}"
        return "Ran shell command", "Ran shell command"

    elif tool_name == "WebFetch":
        url = data.get("url", "")
        prompt = data.get("prompt", "")
        if url:
            # Extract domain
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                title = f"Fetched {domain}"
            except Exception:
                title = f"Fetched a web page"
            if prompt:
                return title, f"{title}: {_snip(prompt, 100)}"
            return title, title
        return "Fetched a web page", "Fetched a web page"

    elif tool_name == "WebSearch":
        query = data.get("query", "")
        title = f"Searched web for '{_snip(query, 40)}'" if query else "Searched the web"
        return title, title

    elif tool_name == "NotebookEdit":
        title = f"Edited notebook {file_str}" if file_str else "Edited a notebook"
        mode = data.get("edit_mode", "replace")
        if mode == "insert":
            title = f"Added cell to {file_str}" if file_str else "Added notebook cell"
        elif mode == "delete":
            title = f"Deleted cell from {file_str}" if file_str else "Deleted notebook cell"
        return title, title

    # Generic fallback
    title = f"Used {tool_name}"
    if file_str:
        title += f" on {file_str}"
    return title, title


def _snip(text: str, max_len: int) -> str:
    """Truncate text cleanly, adding ellipsis if needed."""
    text = text.strip().split("\n")[0].strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 1].rstrip() + "\u2026"


def _count_matches(output: str) -> int | None:
    """Try to count matches/results from tool output."""
    lines = output.strip().split("\n")
    lines = [l for l in lines if l.strip()]
    if not lines:
        return 0
    # For grep/glob, each line is typically a match
    return len(lines)


def _extract_primary_command(cmd: str) -> str:
    """Extract the meaningful part of a shell command."""
    cmd = cmd.strip()
    # Skip cd prefix
    if cmd.startswith("cd ") and "&&" in cmd:
        cmd = cmd.split("&&", 1)[1].strip()
    # Take first command if piped/chained
    for sep in (" | ", " && ", " ; "):
        if sep in cmd:
            cmd = cmd.split(sep)[0].strip()
            break
    return cmd


def generate_session_summary(observations: list[dict],
                              session_id: str) -> dict | None:
    """Generate a session-level summary from individual observations.

    Built structurally from observation data — no LLM needed.
    Produces a title like "Worked on viewer.html, kg.py (12 edits, 5 searches)"
    """
    if not observations:
        return None

    # Collect files and count actions
    all_files = []
    action_counts = {}
    titles = []

    for o in observations:
        try:
            files = json.loads(o.get("files", "[]")) if isinstance(o.get("files"), str) else o.get("files", [])
            all_files.extend(files)
        except (json.JSONDecodeError, TypeError):
            pass
        obs_type = o.get("observation_type", o.get("type", "change"))
        action_counts[obs_type] = action_counts.get(obs_type, 0) + 1
        titles.append(o.get("title", ""))

    # Deduplicate files, keep order
    seen = set()
    unique_files = []
    for f in all_files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    # Build title from most-touched files
    file_counts = {}
    for f in all_files:
        short = f.split("/")[-1]
        file_counts[short] = file_counts.get(short, 0) + 1

    top_files = sorted(file_counts.keys(), key=lambda f: file_counts[f], reverse=True)[:3]

    # Build action summary — include richer types when present
    action_parts = []
    type_labels = [
        ("bugfix", "bug fixes"), ("feature", "features"), ("refactor", "refactors"),
        ("decision", "decisions"), ("change", "edits"), ("discovery", "reads"),
    ]
    for action, label in type_labels:
        count = action_counts.get(action, 0)
        if count:
            action_parts.append(f"{count} {label}")

    action_str = ", ".join(action_parts) if action_parts else f"{len(observations)} actions"

    if top_files:
        title = f"Worked on {', '.join(top_files)} ({action_str})"
    else:
        title = f"Session with {action_str}"

    # Build a richer summary from unique observation titles
    # Filter out boring/repetitive ones
    seen_titles = set()
    notable = []
    for t in titles:
        t_lower = t.lower().strip()
        if t_lower not in seen_titles and len(t) > 10:
            seen_titles.add(t_lower)
            notable.append(t)

    if notable:
        summary = title + ". " + "; ".join(notable[:8])
    else:
        summary = title

    return {
        "type": "session_summary",
        "title": title[:80],
        "summary": summary[:500],
        "files": unique_files[:10],
    }



def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n[...truncated]"


# ── Batch Processor ───────────────────────────────────────────

class ObservationProcessor:
    """Processes queued observations in batches."""

    def __init__(self, config: Config):
        self.config = config
        self.db = MemorableDB(
            Path(config["db_path"]),
            sync_url=config.get("sync_url", ""),
            auth_token=config.get("sync_auth_token", ""),
        )

    def process_queue(self):
        """Process all pending observations, then extract KG data."""
        pending = self.db.get_pending_observations(limit=50)
        if not pending:
            return

        groups = self._group_and_deduplicate(pending)
        new_observations = []

        for group in groups:
            try:
                obs = generate_observation(group)
                if obs:
                    embed_str = f"{obs['title']}. {obs['summary']}"
                    embedding = embed_text(embed_str)

                    obs_id = self.db.store_observation(
                        session_id=group["session_id"],
                        obs_type=obs["type"],
                        title=obs["title"],
                        summary=obs["summary"],
                        files=json.dumps(obs["files"]),
                        embedding=embedding,
                        tool_name=group["tool_name"],
                    )
                    for qid in group["_queue_ids"]:
                        self.db.mark_observation_queued(qid, obs_id)
                    new_observations.append({
                        "session_id": group["session_id"],
                        "title": obs["title"],
                        "summary": obs["summary"],
                        "tool_name": group["tool_name"],
                        "tool_input": group.get("tool_input", "")[:500],
                        "tool_response": group.get("tool_response", "")[:500],
                    })
                else:
                    for qid in group["_queue_ids"]:
                        self.db.mark_observation_queued(qid)
            except Exception as e:
                print(f"  Observation error: {e}")
                for qid in group.get("_queue_ids", []):
                    self.db.mark_observation_queued(qid)

        # KG extraction on new observations
        if new_observations:
            try:
                from .kg import KGProcessor
                if not hasattr(self, '_kg_processor'):
                    self._kg_processor = KGProcessor(self.config)
                result = self._kg_processor.process_observations(new_observations)
                if result["entities_added"] or result["relationships_added"]:
                    print(f"  KG: +{result['entities_added']} entities, "
                          f"+{result['relationships_added']} relationships")
            except Exception as e:
                print(f"  KG extraction error: {e}")

    def _group_and_deduplicate(self, pending: list[dict]) -> list[dict]:
        """Group consecutive tool calls and merge redundant ones."""
        if not pending:
            return []

        groups = []
        current = None

        for item in pending:
            tool = item["tool_name"]

            # Merge consecutive reads/greps on the same patterns
            if (current and current["tool_name"] == tool
                    and tool in ("Read", "Grep", "Glob")
                    and item["session_id"] == current["session_id"]
                    and (item["created_at"] - current["created_at"]) < 60):
                current["_queue_ids"].append(item["id"])
                # Append file info to tool_input
                current["tool_input"] += f"\n{item['tool_input'][:200]}"
                current["tool_response"] += f"\n{item['tool_response'][:200]}"
            else:
                if current:
                    groups.append(current)
                current = {
                    **item,
                    "_queue_ids": [item["id"]],
                }

        if current:
            groups.append(current)

        return groups
