"""Apple-native observation pipeline for Memorable.

Uses Apple Foundation Model (afm) for observation generation and
Apple NaturalLanguage framework for sentence embeddings. Runs
entirely on-device via the Neural Engine — zero API cost.

If quality is insufficient, swap _call_apple_model and embed_text
implementations. The interface stays the same.
"""

import array
import json
import subprocess
from pathlib import Path

from .db import MemorableDB
from .config import Config

# ── Apple NaturalLanguage Embeddings ──────────────────────────

_nl_embedding = None


def _get_embedding_model():
    """Lazy-load Apple NLEmbedding sentence model."""
    global _nl_embedding
    if _nl_embedding is None:
        import objc
        ns = {}
        objc.loadBundle(
            'NaturalLanguage', ns,
            '/System/Library/Frameworks/NaturalLanguage.framework',
        )
        NLEmbedding = ns['NLEmbedding']
        _nl_embedding = NLEmbedding.sentenceEmbeddingForLanguage_('en')
    return _nl_embedding


def embed_text(text: str) -> bytes | None:
    """Generate 512-dim sentence embedding, packed as float32 BLOB."""
    try:
        model = _get_embedding_model()
        if model is None:
            return None
        vec = model.vectorForString_(text[:500])
        if vec is None:
            return None
        return array.array('f', vec).tobytes()
    except Exception:
        return None


def cosine_distance(text1: str, text2: str) -> float:
    """Cosine distance between two texts. Lower = more similar."""
    try:
        model = _get_embedding_model()
        if model is None:
            return 2.0
        return model.distanceBetweenString_andString_distanceType_(
            text1[:500], text2[:500], 0
        )
    except Exception:
        return 2.0


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

OBSERVATION_PROMPT = """\
What did this tool call do? Answer in ONE short sentence, max 15 words. No preamble.

Tool: {tool_name}
Input: {tool_input}
Output: {tool_output}\
"""

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


def generate_observation(tool_data: dict) -> dict | None:
    """Generate a structured observation from a queued tool event."""
    tool_name = tool_data.get("tool_name", "")
    if tool_name in SKIP_TOOLS:
        return None

    tool_input_raw = tool_data.get("tool_input", "")
    tool_output_raw = tool_data.get("tool_response", "")

    # Skip trivial outputs
    if len(tool_output_raw.strip()) < 20:
        return None

    action = _TOOL_ACTION.get(tool_name, "run")
    files = _extract_files(tool_input_raw)

    # Ask afm for just a one-sentence description
    tool_input = _truncate(tool_input_raw, 600)
    tool_output = _truncate(tool_output_raw, 800)

    prompt = OBSERVATION_PROMPT.format(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
    )

    sentence = _call_afm(prompt).strip()

    # Clean up: take just the first sentence, strip quotes/prefixes
    if sentence:
        sentence = sentence.split("\n")[0].strip().strip('"').strip("'")
        # Remove common afm preamble
        for prefix in ("It ", "The tool ", "This tool "):
            if sentence.startswith(prefix) and len(sentence) > 30:
                break

    # Fallback if afm gives garbage or nothing
    if not sentence or len(sentence) < 5 or len(sentence) > 200:
        sentence = _mechanical_description(tool_name, files, tool_output_raw)

    return {
        "type": _ACTION_TO_TYPE.get(action, "change"),
        "action": action,
        "title": sentence[:80],
        "summary": sentence[:200],
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


def _mechanical_description(tool_name: str, files: list[str], output: str) -> str:
    """Fallback description when afm fails — purely mechanical."""
    file_str = files[0].split("/")[-1] if files else ""
    if tool_name == "Read":
        return f"Read {file_str}" if file_str else "Read a file"
    elif tool_name == "Edit":
        return f"Edited {file_str}" if file_str else "Edited a file"
    elif tool_name == "Write":
        return f"Wrote {file_str}" if file_str else "Wrote a file"
    elif tool_name in ("Grep", "Glob"):
        return f"Searched codebase"
    elif tool_name == "Bash":
        return f"Ran shell command"
    return f"Used {tool_name}"


def generate_session_summary(observations: list[dict],
                              session_id: str) -> dict | None:
    """Generate a session-level summary from individual observations."""
    if not observations:
        return None

    obs_lines = "\n".join(
        f"- {o['title']}"
        for o in observations[:20]
    )

    prompt = f"""\
What was accomplished in this coding session? Answer in ONE sentence, max 25 words.

Things that happened:
{obs_lines}\
"""

    sentence = _call_afm(prompt).strip()
    if not sentence or len(sentence) < 5:
        return None

    # Take first sentence, clean up
    sentence = sentence.split("\n")[0].strip().strip('"').strip("'")[:200]

    # Collect all files from observations
    all_files = []
    for o in observations:
        try:
            files = json.loads(o.get("files", "[]")) if isinstance(o.get("files"), str) else o.get("files", [])
            all_files.extend(files)
        except (json.JSONDecodeError, TypeError):
            pass
    # Deduplicate, keep order
    seen = set()
    unique_files = []
    for f in all_files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    return {
        "type": "session_summary",
        "title": sentence[:80],
        "summary": sentence,
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
