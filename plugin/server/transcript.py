"""Transcript extraction for Memorable.

Pure mechanical extraction from JSONL transcripts — no LLM, no DB,
no embeddings. Produces structured data and fact sheets for Haiku summarization.
"""

import json
import re
from collections import Counter
from pathlib import Path

from .constants import SKIP_TOOLS

# ── Constants ────────────────────────────────────────────────

_EMOTION_WORDS = {
    "frustrated", "annoyed", "confused", "stuck", "broken", "angry",
    "happy", "excited", "love", "hate", "worried", "scared", "tired",
    "overwhelmed", "relieved", "surprised", "disappointed", "proud",
    "curious", "weird", "cool", "amazing", "terrible", "awful",
    "beautiful", "fuck", "shit", "damn", "hell", "lol", "lmao",
    "haha", "omg", "wow", "ugh", "sigh", "yay", "finally",
    "honestly", "actually", "literally", "seriously", "basically",
    "remember", "forget", "miss", "wish", "hope", "dream",
    "impossible", "perfect", "insane", "ridiculous", "absurd",
}

_TURNING_PHRASES = {
    "wait", "actually", "never mind", "hold on", "oh no", "oh shit",
    "that worked", "it works", "it broke", "still broken", "still wrong",
    "i think", "i feel", "i just want", "can we", "should we",
    "let's just", "fuck it", "scrap that", "pivot", "dead end",
    "i give up", "one more", "try again", "go nuclear",
}

_TOOL_ACTIONS = {
    "Read": "read", "Grep": "searched", "Glob": "searched",
    "Edit": "edited", "Write": "wrote", "Bash": "ran",
    "WebFetch": "fetched", "WebSearch": "searched web",
    "NotebookEdit": "edited notebook",
}


# ── Extract ──────────────────────────────────────────────────

def extract_session_data(jsonl_path: Path) -> dict:
    """Extract all mechanically-available data from a JSONL transcript."""
    messages = []
    tool_calls = []

    with open(jsonl_path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = entry.get("type")

            if etype == "user":
                msg = entry.get("message", {})
                content = msg.get("content")
                if not content:
                    continue

                if isinstance(content, str):
                    text = _clean_text(content)
                    if text and not text.startswith("You are"):
                        messages.append({"role": "user", "text": text})

                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type", "")

                        if block_type == "tool_result":
                            result_content = block.get("content", "")
                            tool_use_id = block.get("tool_use_id", "")
                            if isinstance(result_content, str):
                                if "[Signal from" in result_content:
                                    sig_text = _extract_signal_text(result_content)
                                    if sig_text:
                                        messages.append({"role": "user", "text": sig_text})
                                _attach_result_to_tool(tool_calls, tool_use_id, result_content)

                        elif block_type == "text":
                            text = _clean_text(block.get("text", ""))
                            if text and len(text) > 5 and _looks_like_human_text(text):
                                messages.append({"role": "user", "text": text})

            elif etype == "assistant":
                msg = entry.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, str):
                    text = _clean_text(content)
                    if text:
                        messages.append({"role": "assistant", "text": text})
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text = _clean_text(block.get("text", ""))
                                if text:
                                    messages.append({"role": "assistant", "text": text})
                            elif block.get("type") == "tool_use":
                                tool_calls.append({
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "input": block.get("input", {}),
                                    "result": "",
                                })

    user_messages = [m for m in messages if m["role"] == "user"]
    assistant_messages = [m for m in messages if m["role"] == "assistant"]

    tool_counts = Counter()
    files_touched = []
    errors = []
    action_descriptions = []

    for tc in tool_calls:
        name = tc["name"]
        if name in SKIP_TOOLS:
            continue
        tool_counts[name] += 1

        inp = tc["input"]
        for key in ("file_path", "path", "notebook_path"):
            if key in inp and inp[key]:
                files_touched.append(inp[key])

        if name == "Bash" and tc.get("result"):
            result_text = tc["result"][:500]
            if _looks_like_error(result_text):
                cmd = inp.get("command", inp.get("description", ""))
                errors.append({
                    "command": cmd[:120],
                    "error": _extract_error_line(result_text),
                })

        desc = _describe_action(tc)
        if desc:
            action_descriptions.append(desc)

    seen_files = set()
    unique_files = []
    for f in files_touched:
        short = _short_path(f)
        if short not in seen_files:
            seen_files.add(short)
            unique_files.append(short)

    user_words = sum(len(m["text"].split()) for m in user_messages)
    total_words = sum(len(m["text"].split()) for m in messages)

    return {
        "messages": messages,
        "user_messages": user_messages,
        "assistant_messages": assistant_messages,
        "tool_calls": tool_calls,
        "tool_counts": dict(tool_counts),
        "files_touched": unique_files,
        "errors": errors,
        "action_descriptions": action_descriptions,
        "message_count": len(messages),
        "user_word_count": user_words,
        "total_word_count": total_words,
    }


# ── Fact Sheet ───────────────────────────────────────────────

def build_fact_sheet(data: dict) -> str:
    """Build a structured fact sheet from extracted session data."""
    sections = []

    opening = _find_opening_request(data["user_messages"])
    if opening:
        sections.append(f"OPENING REQUEST:\n{opening}")

    actions = data.get("action_descriptions", [])
    if actions:
        deduped = _deduplicate_actions(actions)
        sampled = _sample_actions(deduped, max_items=20)
        sections.append("KEY ACTIONS (chronological):\n" + "\n".join(f"- {a}" for a in sampled))

    files = data.get("files_touched", [])
    if files:
        sections.append("FILES TOUCHED:\n" + ", ".join(files[:12]))

    tc = data.get("tool_counts", {})
    if tc:
        parts = []
        edits = tc.get("Edit", 0) + tc.get("Write", 0)
        reads = tc.get("Read", 0)
        searches = tc.get("Grep", 0) + tc.get("Glob", 0)
        bash = tc.get("Bash", 0)
        web = tc.get("WebSearch", 0) + tc.get("WebFetch", 0)
        if edits: parts.append(f"{edits} file edits")
        if reads: parts.append(f"{reads} file reads")
        if searches: parts.append(f"{searches} code searches")
        if bash: parts.append(f"{bash} shell commands")
        if web: parts.append(f"{web} web lookups")
        if parts:
            sections.append("TOOL USAGE: " + ", ".join(parts))

    errors = data.get("errors", [])
    if errors:
        error_lines = []
        for e in errors[:5]:
            error_lines.append(f"- {e['command'][:80]} → {e['error'][:100]}")
        sections.append("ERRORS HIT:\n" + "\n".join(error_lines))

    quotes = _select_quotes(data["user_messages"], max_quotes=8)
    if quotes:
        sections.append("USER QUOTES (verbatim, chronological):\n" + "\n".join(f'- "{q}"' for q in quotes))

    ending = _find_session_ending(data["user_messages"], data["assistant_messages"])
    if ending:
        sections.append(f"SESSION ENDING:\n{ending}")

    sections.append(
        f"STATS: {data['message_count']} messages, "
        f"{data['user_word_count']} user words, "
        f"{data['total_word_count']} total words"
    )

    return "\n\n".join(sections)


# ── Helpers ──────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    text = re.sub(r'<observation>.*?</observation>', '', text, flags=re.DOTALL)
    text = re.sub(r'PROGRESS SUMMARY[:\s].*?(?=\n\n|\Z)', '', text, flags=re.DOTALL)
    text = re.sub(r'^\s*\d+→\s?', '', text, flags=re.MULTILINE)
    match = re.search(r'\[Signal from \w+[^\]]*\]\s*(.+)', text, re.DOTALL)
    if match:
        text = match.group(1)
    return text.strip()


def _extract_signal_text(text: str) -> str | None:
    match = re.search(r'\[Signal from \w+[^\]]*\]\s*(.+)', text, re.DOTALL)
    return match.group(1).strip() if match else None


def _looks_like_human_text(text: str) -> bool:
    if any(text.startswith(p) for p in [
        "You are", "Caveat:", "PROGRESS SUMMARY",
        "Claude's Full Response", "Full Response to User",
        "Called the", "Result of calling",
        "Base directory for this skill",
        "Available skills", "Note:", "Contents of",
    ]):
        return False
    if text.count("→") > 2 or text.count("│") > 3:
        return False
    if len(text) > 1500:
        return False
    if text.count("```") > 1 or text.count("def ") > 2 or text.count("import ") > 3:
        return False
    return True


def _attach_result_to_tool(tool_calls: list, tool_use_id: str, result: str):
    for tc in reversed(tool_calls):
        if tc["id"] == tool_use_id:
            tc["result"] = result[:2000]
            return


def _looks_like_error(text: str) -> bool:
    error_patterns = [
        "error:", "Error:", "ERROR", "traceback", "Traceback",
        "failed", "Failed", "FAILED", "fatal:", "Fatal:",
        "exception", "Exception", "permission denied",
        "not found", "No such file", "command not found",
        "exit code", "returned non-zero",
    ]
    return any(p in text for p in error_patterns)


def _extract_error_line(text: str) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if any(p in line for p in ["Error:", "error:", "ERROR", "Traceback", "fatal:"]):
            return line[:150]
    return text.split("\n")[0][:150]


def _describe_action(tool_call: dict) -> str:
    name = tool_call["name"]
    inp = tool_call.get("input", {})

    if name == "Read":
        path = _short_path(inp.get("file_path", ""))
        return f"Read {path}" if path else None
    elif name == "Edit":
        path = _short_path(inp.get("file_path", ""))
        return f"Edited {path}" if path else None
    elif name == "Write":
        path = _short_path(inp.get("file_path", ""))
        content = inp.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"Wrote {path} ({lines} lines)" if path else None
    elif name == "Bash":
        desc = inp.get("description", "")
        if desc:
            return desc[:80]
        cmd = inp.get("command", "")
        if cmd:
            return f"Ran: {_extract_primary_command(cmd)[:60]}"
        return None
    elif name == "Grep":
        pattern = inp.get("pattern", "")
        return f"Searched for '{pattern[:40]}'" if pattern else None
    elif name == "Glob":
        pattern = inp.get("pattern", "")
        return f"Found files matching '{pattern[:40]}'" if pattern else None
    elif name == "WebSearch":
        query = inp.get("query", "")
        return f"Web searched: '{query[:50]}'" if query else None
    elif name == "WebFetch":
        url = inp.get("url", "")
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            return f"Fetched {domain}"
        except Exception:
            return "Fetched a web page"
    return f"Used {name}"


def _extract_primary_command(cmd: str) -> str:
    cmd = cmd.strip()
    if cmd.startswith("cd ") and "&&" in cmd:
        cmd = cmd.split("&&", 1)[1].strip()
    for sep in (" | ", " && ", " ; "):
        if sep in cmd:
            cmd = cmd.split(sep)[0].strip()
            break
    return cmd


def _short_path(p: str) -> str:
    if not p:
        return ""
    parts = p.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]


def _find_opening_request(user_messages: list[dict]) -> str | None:
    for msg in user_messages[:8]:
        text = msg["text"].strip()
        words = text.split()
        if len(words) < 5:
            continue
        if any(text.startswith(p) for p in [
            "You are", "ToolSearch", "Read", "Caveat:", "PROGRESS SUMMARY",
            "Claude's Full Response", "Contents of", "Base directory",
        ]):
            continue
        if text.count("\n") > 10 or text.count("→") > 3:
            continue
        first_line = text.split("\n")[0].strip()
        return first_line[:300]
    return None


def _deduplicate_actions(actions: list[str]) -> list[str]:
    deduped = []
    prev = ""
    for action in actions:
        key = action.split()[0] if action else ""
        prev_key = prev.split()[0] if prev else ""
        if key == prev_key and key in ("Read", "Searched"):
            if action != prev:
                deduped.append(action)
        else:
            deduped.append(action)
        prev = action
    return deduped


def _sample_actions(actions: list[str], max_items: int = 20) -> list[str]:
    if len(actions) <= max_items:
        return actions
    head = actions[:6]
    tail = actions[-4:]
    middle_count = max_items - len(head) - len(tail)
    middle = actions[6:-4]
    step = max(1, len(middle) // middle_count)
    middle_sampled = middle[::step][:middle_count]
    return head + middle_sampled + tail


def _select_quotes(user_messages: list[dict], max_quotes: int = 8) -> list[str]:
    candidates = []
    for i, msg in enumerate(user_messages):
        text = msg["text"].strip()
        words = text.split()
        if len(words) < 4:
            continue
        if any(text.startswith(p) for p in [
            "You are", "ToolSearch", "Read", "Caveat:", "PROGRESS SUMMARY",
            "Claude's Full Response", "Contents of",
        ]):
            continue
        if len(words) > 80 or text.count("\n") > 5:
            continue

        score = 0.0
        text_lower = text.lower()

        if "?" in text:
            score += 2.0
        emotion_count = sum(1 for w in words if w.lower().rstrip(".,!?") in _EMOTION_WORDS)
        score += emotion_count * 1.5
        for phrase in _TURNING_PHRASES:
            if phrase in text_lower:
                score += 2.0
                break
        if "!" in text:
            score += 1.0
        if i <= 2:
            score += 1.5
        if i >= len(user_messages) - 3:
            score += 1.0
        if 10 < len(words) < 40:
            score += 1.0

        if score > 0:
            first_line = text.split("\n")[0][:200]
            candidates.append((score, i, first_line))

    candidates.sort(key=lambda x: -x[0])
    selected = candidates[:max_quotes]
    selected.sort(key=lambda x: x[1])
    return [c[2] for c in selected]


def _find_session_ending(user_messages: list[dict], assistant_messages: list[dict]) -> str | None:
    parts = []
    for msg in user_messages[-2:]:
        text = msg["text"].split("\n")[0][:150]
        if len(text.split()) >= 2:
            parts.append(f'User: "{text}"')
    for msg in assistant_messages[-1:]:
        text = msg["text"].split("\n")[0][:150]
        if len(text.split()) >= 3:
            parts.append(f'Claude: "{text}"')
    return "\n".join(parts) if parts else None
