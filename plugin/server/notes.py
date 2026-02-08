"""Decomposed session notes generator for Memorable.

Produces structured session notes matching the DeepSeek exemplar format:
  - YAML frontmatter (date, tags, mood, continuity)
  - Summary paragraph
  - Key Moments (selected quotes)
  - Notes for Future Me (bullets)

The pipeline:
  1. EXTRACT — mechanical extraction from JSONL transcripts (no LLM)
  2. COMPOSE — AFM for mood/bullets, Haiku for summary paragraph
  3. FORMAT — markdown with YAML frontmatter

See SESSION_NOTES_BRIEF.md for the full design rationale.
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from collections import Counter

from .db import MemorableDB
from .constants import SKIP_TOOLS

logger = logging.getLogger(__name__)


# ── Emotional / salience keywords for quote selection ────────

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

# _SKIP_TOOLS imported from constants.py
_SKIP_TOOLS = SKIP_TOOLS

# Map tool names to action verbs
_TOOL_ACTIONS = {
    "Read": "read", "Grep": "searched", "Glob": "searched",
    "Edit": "edited", "Write": "wrote", "Bash": "ran",
    "WebFetch": "fetched", "WebSearch": "searched web",
    "NotebookEdit": "edited notebook",
}


# ── EXTRACT: Mechanical data from JSONL ──────────────────────

def extract_session_data(jsonl_path: Path) -> dict:
    """Extract all mechanically-available data from a JSONL transcript.

    Returns a dict with all the raw materials needed for note composition.
    """
    messages = []
    tool_calls = []
    all_text_blocks = []

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
                    # Direct string content = actual human message
                    text = _clean_text(content)
                    if text and not text.startswith("You are"):
                        messages.append({"role": "user", "text": text})
                        all_text_blocks.append(text)

                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type", "")

                        if block_type == "tool_result":
                            # tool_result = machine response, NOT a user message
                            # Only extract Signal messages and error data
                            result_content = block.get("content", "")
                            tool_use_id = block.get("tool_use_id", "")
                            if isinstance(result_content, str):
                                if "[Signal from" in result_content:
                                    sig_text = _extract_signal_text(result_content)
                                    if sig_text:
                                        messages.append({"role": "user", "text": sig_text})
                                        all_text_blocks.append(sig_text)
                                # Store result for error matching with tool calls
                                _attach_result_to_tool(tool_calls, tool_use_id, result_content)

                        elif block_type == "text":
                            # Text blocks in user list entries are usually
                            # injected context (system reminders, hook output)
                            # Only include if they look like real human text
                            text = _clean_text(block.get("text", ""))
                            if text and len(text) > 5 and _looks_like_human_text(text):
                                messages.append({"role": "user", "text": text})
                                all_text_blocks.append(text)

            elif etype == "assistant":
                msg = entry.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, str):
                    text = _clean_text(content)
                    if text:
                        messages.append({"role": "assistant", "text": text})
                        all_text_blocks.append(text)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text = _clean_text(block.get("text", ""))
                                if text:
                                    messages.append({"role": "assistant", "text": text})
                                    all_text_blocks.append(text)
                            elif block.get("type") == "tool_use":
                                tool_calls.append({
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "input": block.get("input", {}),
                                    "result": "",  # filled by _attach_result_to_tool
                                })

    # Derive structured data
    user_messages = [m for m in messages if m["role"] == "user"]
    assistant_messages = [m for m in messages if m["role"] == "assistant"]

    # Tool usage breakdown
    tool_counts = Counter()
    files_touched = []
    errors = []
    action_descriptions = []

    for tc in tool_calls:
        name = tc["name"]
        if name in _SKIP_TOOLS:
            continue
        tool_counts[name] += 1

        # Extract files
        inp = tc["input"]
        for key in ("file_path", "path", "notebook_path"):
            if key in inp and inp[key]:
                files_touched.append(inp[key])

        # Detect errors in Bash output
        if name == "Bash" and tc.get("result"):
            result_text = tc["result"][:500]
            if _looks_like_error(result_text):
                cmd = inp.get("command", inp.get("description", ""))
                errors.append({
                    "command": cmd[:120],
                    "error": _extract_error_line(result_text),
                })

        # Build action description
        desc = _describe_action(tc)
        if desc:
            action_descriptions.append(desc)

    # Deduplicate files, keep order
    seen_files = set()
    unique_files = []
    for f in files_touched:
        short = _short_path(f)
        if short not in seen_files:
            seen_files.add(short)
            unique_files.append(short)

    # Count words
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


def build_fact_sheet(data: dict) -> str:
    """Build a structured fact sheet from extracted session data.

    This is sent to Haiku instead of raw conversation text, producing
    much better narrative summaries. The fact sheet contains:
    - What the user initially asked for
    - Key actions taken (deduplicated, grouped)
    - Errors encountered
    - Files changed
    - Salient user quotes (emotional arc)
    - How the session ended
    """
    sections = []

    # 1. Opening request — what did the user want?
    opening = _find_opening_request(data["user_messages"])
    if opening:
        sections.append(f"OPENING REQUEST:\n{opening}")

    # 2. Action timeline — what happened, in order
    actions = data.get("action_descriptions", [])
    if actions:
        # Deduplicate consecutive similar actions
        deduped = _deduplicate_actions(actions)
        # Take a representative sample (first few, middle, last few)
        sampled = _sample_actions(deduped, max_items=20)
        sections.append("KEY ACTIONS (chronological):\n" + "\n".join(f"- {a}" for a in sampled))

    # 3. Files changed
    files = data.get("files_touched", [])
    if files:
        sections.append("FILES TOUCHED:\n" + ", ".join(files[:12]))

    # 4. Tool usage summary
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

    # 5. Errors encountered
    errors = data.get("errors", [])
    if errors:
        error_lines = []
        for e in errors[:5]:
            cmd = e["command"][:80]
            err = e["error"][:100]
            error_lines.append(f"- {cmd} → {err}")
        sections.append("ERRORS HIT:\n" + "\n".join(error_lines))

    # 6. Salient user quotes (emotional arc + context)
    quotes = _select_fact_sheet_quotes(data["user_messages"], max_quotes=8)
    if quotes:
        sections.append("USER QUOTES (verbatim, chronological):\n" + "\n".join(f'- "{q}"' for q in quotes))

    # 7. How it ended — last few substantive exchanges
    ending = _find_session_ending(data["user_messages"], data["assistant_messages"])
    if ending:
        sections.append(f"SESSION ENDING:\n{ending}")

    # 8. Stats
    sections.append(
        f"STATS: {data['message_count']} messages, "
        f"{data['user_word_count']} user words, "
        f"{data['total_word_count']} total words"
    )

    return "\n\n".join(sections)


def _find_opening_request(user_messages: list[dict]) -> str | None:
    """Find the first substantive user message that sets the session goal."""
    for msg in user_messages[:8]:
        text = msg["text"].strip()
        words = text.split()
        # Skip very short greetings
        if len(words) < 5:
            continue
        # Skip system/hook artifacts
        if any(text.startswith(p) for p in [
            "You are", "ToolSearch", "Read", "Caveat:", "PROGRESS SUMMARY",
            "Claude's Full Response", "Contents of", "Base directory",
        ]):
            continue
        # Skip pasted content
        if text.count("\n") > 10 or text.count("→") > 3:
            continue
        # Return first substantive message, cleaned
        first_line = text.split("\n")[0].strip()
        return first_line[:300]
    return None


def _deduplicate_actions(actions: list[str]) -> list[str]:
    """Remove consecutive duplicates and repetitive patterns."""
    deduped = []
    prev = ""
    for action in actions:
        # Collapse repeated similar reads/searches
        key = action.split()[0] if action else ""  # first word: Read, Edited, Searched...
        prev_key = prev.split()[0] if prev else ""
        if key == prev_key and key in ("Read", "Searched"):
            # Keep if different target
            if action != prev:
                deduped.append(action)
        else:
            deduped.append(action)
        prev = action
    return deduped


def _sample_actions(actions: list[str], max_items: int = 20) -> list[str]:
    """Sample actions: keep first few, last few, and evenly spaced middle."""
    if len(actions) <= max_items:
        return actions
    head = actions[:6]
    tail = actions[-4:]
    middle_count = max_items - len(head) - len(tail)
    middle = actions[6:-4]
    step = max(1, len(middle) // middle_count)
    middle_sampled = middle[::step][:middle_count]
    return head + middle_sampled + tail


def _select_fact_sheet_quotes(user_messages: list[dict], max_quotes: int = 8) -> list[str]:
    """Select diverse user quotes for the fact sheet — captures emotional arc.

    Picks: first request, questions, emotional moments, turning points, ending.
    """
    candidates = []
    for i, msg in enumerate(user_messages):
        text = msg["text"].strip()
        words = text.split()
        if len(words) < 4:
            continue
        # Skip system artifacts
        if any(text.startswith(p) for p in [
            "You are", "ToolSearch", "Read", "Caveat:", "PROGRESS SUMMARY",
            "Claude's Full Response", "Contents of",
        ]):
            continue
        # Skip very long pastes
        if len(words) > 80 or text.count("\n") > 5:
            continue

        score = 0.0
        text_lower = text.lower()

        # Questions
        if "?" in text:
            score += 2.0
        # Emotional words
        emotion_count = sum(1 for w in words if w.lower().rstrip(".,!?") in _EMOTION_WORDS)
        score += emotion_count * 1.5
        # Turning phrases
        for phrase in _TURNING_PHRASES:
            if phrase in text_lower:
                score += 2.0
                break
        # Exclamations
        if "!" in text:
            score += 1.0
        # Position: first substantive message
        if i <= 2:
            score += 1.5
        # Position: last messages
        if i >= len(user_messages) - 3:
            score += 1.0
        # Length bonus for substantive messages
        if 10 < len(words) < 40:
            score += 1.0

        if score > 0:
            first_line = text.split("\n")[0][:200]
            candidates.append((score, i, first_line))

    # Sort by score, take top N
    candidates.sort(key=lambda x: -x[0])
    selected = candidates[:max_quotes]
    # Re-sort by position for chronological order
    selected.sort(key=lambda x: x[1])
    return [c[2] for c in selected]


def _find_session_ending(user_messages: list[dict], assistant_messages: list[dict]) -> str | None:
    """Capture the final exchange to understand how the session concluded."""
    parts = []
    # Last 2 user messages
    for msg in user_messages[-2:]:
        text = msg["text"].split("\n")[0][:150]
        if len(text.split()) >= 2:
            parts.append(f'User: "{text}"')
    # Last assistant message
    for msg in assistant_messages[-1:]:
        text = msg["text"].split("\n")[0][:150]
        if len(text.split()) >= 3:
            parts.append(f'Claude: "{text}"')
    return "\n".join(parts) if parts else None


def _attach_result_to_tool(tool_calls: list, tool_use_id: str, result: str):
    """Attach a tool_result back to its tool_use by ID."""
    for tc in reversed(tool_calls):
        if tc["id"] == tool_use_id:
            tc["result"] = result[:2000]
            return
    # If no match found (e.g. result before tool_use in stream), skip


def _looks_like_error(text: str) -> bool:
    """Check if tool output looks like an error."""
    error_patterns = [
        "error:", "Error:", "ERROR", "traceback", "Traceback",
        "failed", "Failed", "FAILED", "fatal:", "Fatal:",
        "exception", "Exception", "permission denied",
        "not found", "No such file", "command not found",
        "exit code", "returned non-zero",
    ]
    return any(p in text for p in error_patterns)


def _extract_error_line(text: str) -> str:
    """Pull the most informative error line from output."""
    for line in text.split("\n"):
        line = line.strip()
        if any(p in line for p in ["Error:", "error:", "ERROR", "Traceback", "fatal:"]):
            return line[:150]
    return text.split("\n")[0][:150]


def _describe_action(tool_call: dict) -> str:
    """Generate a one-line description of a tool action."""
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
            primary = _extract_primary_command(cmd)
            return f"Ran: {primary[:60]}"
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
    """Extract the meaningful part of a shell command."""
    cmd = cmd.strip()
    if cmd.startswith("cd ") and "&&" in cmd:
        cmd = cmd.split("&&", 1)[1].strip()
    for sep in (" | ", " && ", " ; "):
        if sep in cmd:
            cmd = cmd.split(sep)[0].strip()
            break
    return cmd


def _short_path(p: str) -> str:
    """Last 2 path components."""
    if not p:
        return ""
    parts = p.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]


# ── SELECT: Key Moments ──────────────────────────────────────

def select_key_moments(data: dict, max_quotes: int = 5) -> list[dict]:
    """Select the most interesting/salient user quotes as Key Moments.

    Uses heuristics:
    - Emotional language (keywords)
    - Questions (? marks)
    - Exclamations (! marks)
    - Turning-point phrases
    - Length (substantive messages)
    - Position (first request, final state)

    Also picks 1-2 Claude quotes that show personality.
    """
    user_msgs = data["user_messages"]
    assistant_msgs = data["assistant_messages"]

    if not user_msgs:
        return []

    # Score each user message
    scored = []
    for i, msg in enumerate(user_msgs):
        text = msg["text"]
        # Skip very short messages
        if len(text.split()) < 4:
            continue
        # Skip system/tool artifacts
        if text.startswith(("ToolSearch", "Read", "Edit", "Bash", "mcp__")):
            continue
        if "PROGRESS SUMMARY" in text or "Claude's Full Response" in text:
            continue
        if text.startswith("This session is being continued from"):
            continue

        score = _score_quote(text, i, len(user_msgs))
        if score > 0:
            scored.append({
                "role": "user",
                "text": _clean_quote(text),
                "score": score,
                "position": i,
            })

    # Sort by score, take top N-1 (leave room for a Claude quote)
    scored.sort(key=lambda x: x["score"], reverse=True)
    selected = scored[:max_quotes - 1]

    # Try to find 1 good Claude quote (shows personality, empathy, humor)
    claude_quote = _find_best_claude_quote(assistant_msgs)
    if claude_quote:
        selected.append(claude_quote)

    # Re-sort by position for chronological presentation
    selected.sort(key=lambda x: x["position"])

    return selected


def _score_quote(text: str, position: int, total: int) -> float:
    """Score a user message for Key Moment salience."""
    score = 0.0
    text_lower = text.lower()
    words = text.split()

    # Length bonus (substantive messages, diminishing returns)
    word_count = len(words)
    if word_count < 5:
        return 0  # too short
    if word_count > 10:
        score += min(2.0, word_count / 20)

    # Question marks — indicates curiosity, uncertainty, decisions
    if "?" in text:
        score += 1.5

    # Exclamation marks — emotional intensity
    if "!" in text:
        score += 1.0

    # Emotional keywords
    emotion_count = sum(1 for w in words if w.lower().rstrip(".,!?") in _EMOTION_WORDS)
    score += emotion_count * 1.5

    # Turning-point phrases
    for phrase in _TURNING_PHRASES:
        if phrase in text_lower:
            score += 2.0
            break

    # First substantive message bonus (sets the scene)
    if position <= 2:
        score += 1.0

    # Last few messages bonus (resolution/status)
    if position >= total - 3:
        score += 0.5

    # Penalty for very long messages (probably technical dump, not a moment)
    if word_count > 80:
        score *= 0.5

    # Penalty for messages that look like pasted code/output
    if text.count("\n") > 5 or text.count("  ") > 10:
        score *= 0.3

    return score


def _find_best_claude_quote(assistant_msgs: list[dict]) -> dict | None:
    """Find a Claude message that shows personality (empathy, humor, validation)."""
    personality_signals = [
        "ha,", "ha!", "haha", "fair", "honestly", "yeah,",
        "that's", "genuinely", "not just you", "makes sense",
        "I think", "I feel", "it's not", "to be fair",
    ]

    best = None
    best_score = 0

    for i, msg in enumerate(assistant_msgs):
        text = msg["text"].strip()
        if not text:
            continue
        # Skip long responses (we want short, punchy Claude moments)
        if len(text.split()) > 40 or len(text.split()) < 4:
            continue
        # Skip tool descriptions / technical output
        if text.startswith(("I'll ", "Let me ", "I need to ", "I'm going to ")):
            continue

        score = 0
        text_lower = text.lower()
        for signal in personality_signals:
            if signal in text_lower:
                score += 1

        if score > best_score:
            best_score = score
            best = {
                "role": "assistant",
                "text": _clean_quote(text),
                "score": score,
                "position": i,
            }

    return best if best and best_score >= 1 and best["text"] else None


def _clean_quote(text: str) -> str:
    """Clean a quote for presentation — first sentence/line, strip artifacts."""
    # Take first line
    text = text.split("\n")[0].strip()
    # Strip Signal prefix
    text = re.sub(r'^Signal:\s*', '', text)
    # Strip markdown
    text = re.sub(r'\*\*', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    # Truncate if too long
    if len(text) > 200:
        # Try to cut at sentence boundary
        for end in [".", "!", "?"]:
            idx = text.find(end, 60)
            if idx > 0 and idx < 180:
                return text[:idx + 1]
        return text[:197] + "..."
    return text


# ── COMPOSE: Build the session note ──────────────────────────

def compose_session_note(
    data: dict,
    summary_text: str,
    header: str,
    date: str,
    title: str,
    db: MemorableDB | None = None,
) -> dict:
    """Compose a full session note in the exemplar format.

    Args:
        data: Output from extract_session_data()
        summary_text: Haiku summary (already generated by processor.py)
        header: AFM emoji tags (already generated)
        date: Session date string
        title: Session title
        db: Database for continuity scoring

    Returns:
        Dict with 'note_content' (formatted markdown), 'mood', 'tags' (JSON),
        'continuity' (int), and 'key_moments' (list).
    """
    key_moments = select_key_moments(data)

    # Generate mood line via AFM
    mood = _generate_mood(data, header)

    # Calculate continuity score
    continuity = _calculate_continuity(data, db) if db else 5

    # Build tags from header (already emoji-tagged by AFM)
    tags = _header_to_tags(header)

    # Use Haiku for quote annotations + insightful Notes for Future Me
    # This is ONE additional Haiku call — produces both outputs
    enriched = _haiku_enrich_notes(key_moments, summary_text, data)
    if enriched:
        # Apply annotations to key moments
        for moment in key_moments:
            annotation = enriched.get("annotations", {}).get(moment["text"], "")
            if annotation:
                moment["annotation"] = annotation
        # Use Haiku's insightful notes instead of mechanical ones
        notes = enriched.get("notes", []) or _generate_future_notes(data, summary_text)
    else:
        notes = _generate_future_notes(data, summary_text)

    # Format the complete note
    note_content = _format_note(
        date=date,
        tags=tags,
        mood=mood,
        continuity=continuity,
        summary=summary_text,
        key_moments=key_moments,
        notes=notes,
    )

    return {
        "note_content": note_content,
        "mood": mood,
        "tags": json.dumps(tags),
        "continuity": continuity,
        "key_moments": key_moments,
    }


def _haiku_enrich_notes(key_moments: list[dict], summary_text: str, data: dict) -> dict | None:
    """Enrich key moments with annotations and generate insightful notes via Haiku.

    ONE Haiku call that produces:
    1. Short annotations for each quote (3-8 words capturing why the quote matters)
    2. 3-4 insightful "Notes for Future Me" bullets with bold labels

    Returns dict with 'annotations' (quote_text -> annotation) and 'notes' (list of strings).
    Returns None on failure, falling back to mechanical notes.
    """
    if not key_moments or not summary_text:
        return None

    from .llm import call_llm_json

    # Build the quotes section
    quotes_block = []
    for i, m in enumerate(key_moments):
        speaker = "Matt" if m["role"] == "user" else "Claude"
        quotes_block.append(f'{i+1}. {speaker}: "{m["text"]}"')
    quotes_text = "\n".join(quotes_block)

    # Build context from errors and files
    context_parts = []
    errors = data.get("errors", [])
    if errors:
        context_parts.append(f"Errors hit: {len(errors)}")
        for e in errors[:3]:
            context_parts.append(f"  - {e['command'][:60]} → {e['error'][:80]}")
    files = data.get("files_touched", [])
    if files:
        context_parts.append(f"Files: {', '.join(files[:8])}")
    context_text = "\n".join(context_parts)

    prompt = (
        f"SUMMARY:\n{summary_text}\n\n"
        f"KEY QUOTES:\n{quotes_text}\n\n"
        f"CONTEXT:\n{context_text}\n\n"
        f"Respond with JSON only."
    )

    system = (
        "You annotate conversation quotes and write follow-up notes.\n\n"
        "Given a session summary and selected quotes, output JSON with:\n"
        "1. \"annotations\": object mapping each quote's EXACT text to a 3-8 word "
        "annotation capturing WHY it matters (emotional beat, turning point, humor). "
        "Examples: \"the moment the simple path vanished\", \"finding humor in the absurdity\", "
        "\"a vulnerable admission of complexity\", \"resigned recognition\".\n"
        "2. \"notes\": array of 3-4 bullet strings for 'Notes for Future Me'. Each bullet "
        "starts with a **Bold Label:** then detail. Labels like: **Unfinished:**, **Context:**, "
        "**Tech Detail:**, **Key Decision:**, **Next Step:**, **Relational:**\n"
        "Notes should capture: what's unfinished, emotional/relational context, "
        "technical details worth remembering, and status of things that changed.\n\n"
        "The user is Matt. Write notes as if Claude is leaving them for a future Claude instance.\n"
        "Output ONLY valid JSON. No markdown fences, no commentary."
    )

    result = call_llm_json(prompt, system=system, model="haiku")
    if not result:
        return None

    # Validate structure
    annotations = result.get("annotations", {})
    notes = result.get("notes", [])

    if not isinstance(annotations, dict) or not isinstance(notes, list):
        return None

    # Clean up notes — ensure they have bold labels
    cleaned_notes = []
    for note in notes[:5]:
        if isinstance(note, str) and note.strip():
            # Ensure it starts with **Label:**
            if not note.strip().startswith("**"):
                note = f"**Note:** {note}"
            cleaned_notes.append(note.strip())

    return {
        "annotations": annotations,
        "notes": cleaned_notes or None,
    }


def _generate_mood(data: dict, header: str) -> str:
    """Generate a mood line from user quotes and session context.

    Uses AFM with the -i flag for instruction-following.
    Falls back to heuristic if AFM fails.
    """
    user_msgs = data["user_messages"]
    if not user_msgs:
        return "A quiet session"

    # Pick a sample of user quotes for mood inference
    sample_quotes = []
    for msg in user_msgs[:15]:
        text = msg["text"].split("\n")[0][:100]
        if len(text.split()) >= 3:
            sample_quotes.append(text)

    if not sample_quotes:
        return "A working session"

    quotes_text = "\n".join(f"- \"{q}\"" for q in sample_quotes[:8])
    errors = data.get("errors", [])

    context_hints = []
    if errors:
        context_hints.append(f"{len(errors)} errors encountered")
    tool_counts = data.get("tool_counts", {})
    if tool_counts.get("Edit", 0) > 5:
        context_hints.append("heavy editing session")
    if tool_counts.get("WebSearch", 0) > 0:
        context_hints.append("research involved")

    hints_str = f"\nSession context: {', '.join(context_hints)}" if context_hints else ""

    prompt = f"User quotes from a conversation:\n{quotes_text}{hints_str}"
    instructions = (
        "Write ONE mood/tone line (under 15 words) capturing the emotional "
        "texture of this conversation. Be specific and evocative, not generic. "
        "Examples: 'A frustrating technical maze, navigated with stubborn humor', "
        "'System caretaking with a side of gentle teasing', "
        "'Technical roadblocks met with quiet, shared disappointment'. "
        "Output ONLY the mood line, nothing else."
    )

    try:
        env = {**__import__("os").environ, "TOKENIZERS_PARALLELISM": "false"}
        result = subprocess.run(
            ["afm", "-s", prompt, "-i", instructions],
            capture_output=True, text=True, timeout=30, env=env,
        )
        output = result.stdout.strip()
        if output and len(output) < 100 and "I apologize" not in output:
            # Clean up: remove quotes, trailing period
            output = output.strip('"').strip("'").rstrip(".")
            return output
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: heuristic mood
    return _heuristic_mood(data)


def _heuristic_mood(data: dict) -> str:
    """Generate a basic mood line from data patterns."""
    errors = data.get("errors", [])
    user_msgs = data["user_messages"]
    tool_counts = data.get("tool_counts", {})

    has_questions = any("?" in m["text"] for m in user_msgs[:10])
    has_frustration = any(
        w in " ".join(m["text"].lower() for m in user_msgs)
        for w in ["frustrat", "stuck", "broken", "fuck", "shit", "confused"]
    )
    has_humor = any(
        w in " ".join(m["text"].lower() for m in user_msgs)
        for w in ["lol", "haha", "lmao", "funny", "😂"]
    )
    heavy_editing = tool_counts.get("Edit", 0) > 5

    if has_frustration and has_humor:
        return "Debugging frustrations met with persistent humor"
    elif has_frustration:
        return "A grinding technical session with stubborn problems"
    elif heavy_editing and not errors:
        return "Focused building, steady progress"
    elif has_questions:
        return "Exploratory and curious, mapping new territory"
    elif errors:
        return "Troubleshooting through a series of obstacles"
    else:
        return "A collaborative working session"


def _calculate_continuity(data: dict, db: MemorableDB) -> int:
    """Calculate continuity score (1-10) based on entity overlap with recent sessions.

    Higher score = more connected to ongoing threads.
    """
    try:
        # Get entities from this session's keywords/metadata
        all_text = " ".join(m["text"] for m in data["messages"][:20])

        # Get recent session entities from KG
        recent_entities = set()
        entities = db.query_kg(min_priority=4, limit=200)
        for e in entities:
            recent_entities.add(e["name"].lower())

        if not recent_entities:
            return 5  # default

        # Count how many known entities appear in this session
        matches = 0
        for entity_name in recent_entities:
            if entity_name.lower() in all_text.lower():
                matches += 1

        # Scale: 0 matches = 3, 1-2 = 5, 3-5 = 6, 6-10 = 7, 10+ = 8
        if matches == 0:
            return 3
        elif matches <= 2:
            return 5
        elif matches <= 5:
            return 6
        elif matches <= 10:
            return 7
        else:
            return min(9, 7 + matches // 10)

    except Exception:
        return 5


def _header_to_tags(header: str) -> list[str]:
    """Convert AFM header string to tag list.

    Input:  "🔧 Built auth | 💛 Felt good | ✅ Chose JWT"
    Output: ["🔧 Built auth", "💛 Felt good", "✅ Chose JWT"]
    """
    if not header:
        return []
    tags = [t.strip() for t in header.split("|") if t.strip()]
    return tags[:6]


def _generate_future_notes(data: dict, summary_text: str) -> list[str]:
    """Generate Notes for Future Me bullets.

    Combines:
    - Unfinished items (errors at end of session)
    - Technical details (files changed, tools used)
    - Key facts from the summary
    """
    notes = []
    errors = data.get("errors", [])
    files = data.get("files_touched", [])
    tool_counts = data.get("tool_counts", {})

    # Unfinished: errors at end of session suggest incomplete work
    if errors:
        last_error = errors[-1]
        err = last_error["error"][:100]
        # Make it readable — skip raw tracebacks and long commands
        if "Traceback" in err:
            err = "A Python script encountered an error"
        elif "command not found" in err.lower():
            cmd_name = err.split("command not found")[0].strip().split()[-1] if err else ""
            err = f"Command not found: {cmd_name}" if cmd_name else "A command was not found"
        elif "permission denied" in err.lower():
            err = "Permission denied on a file operation"
        notes.append(f"**Unfinished:** {err}")

    # Technical context: what was modified
    if files:
        top_files = files[:5]
        notes.append(f"**Files changed:** {', '.join(top_files)}")

    # Session scope
    edits = tool_counts.get("Edit", 0) + tool_counts.get("Write", 0)
    searches = tool_counts.get("Grep", 0) + tool_counts.get("Glob", 0) + tool_counts.get("Read", 0)
    bash_runs = tool_counts.get("Bash", 0)
    if edits or searches or bash_runs:
        parts = []
        if edits:
            parts.append(f"{edits} edits")
        if searches:
            parts.append(f"{searches} searches")
        if bash_runs:
            parts.append(f"{bash_runs} commands")
        notes.append(f"**Scope:** {', '.join(parts)}")

    # Try AFM for a contextual note from the summary
    if summary_text and len(summary_text) > 50:
        afm_note = _afm_contextual_note(summary_text)
        if afm_note:
            notes.append(afm_note)

    # Ensure we have at least 2 notes
    if len(notes) < 2:
        notes.append(f"**Session size:** {data['message_count']} messages, {data['total_word_count']} words")

    return notes[:5]


def _afm_contextual_note(summary_text: str) -> str | None:
    """Ask AFM to extract one key takeaway from the summary."""
    snippet = summary_text[:500]
    prompt = f"Session summary: {snippet}"
    instructions = (
        "Write ONE bullet point capturing the most important takeaway or "
        "unfinished item from this session. Format: **Label:** Detail. "
        "Example: '**Key decision:** Switched from X to Y for performance reasons.' "
        "Output ONLY the bullet point, nothing else."
    )

    try:
        env = {**__import__("os").environ, "TOKENIZERS_PARALLELISM": "false"}
        result = subprocess.run(
            ["afm", "-s", prompt, "-i", instructions],
            capture_output=True, text=True, timeout=30, env=env,
        )
        output = result.stdout.strip()
        if output and output.startswith("**") and "I apologize" not in output:
            return output.split("\n")[0][:200]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


# ── FORMAT: Markdown output ──────────────────────────────────

def _format_note(
    date: str,
    tags: list[str],
    mood: str,
    continuity: int,
    summary: str,
    key_moments: list[dict],
    notes: list[str],
) -> str:
    """Format all components into the final session note markdown."""
    lines = []

    # YAML frontmatter
    tags_str = ", ".join(tags) if tags else ""
    lines.append("---")
    lines.append(f"date: {date}")
    if tags_str:
        lines.append(f"tags: [{tags_str}]")
    lines.append(f"mood: {mood}")
    lines.append(f"continuity: {continuity}")
    lines.append("---")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append(summary.strip())
    lines.append("")

    # Key Moments
    if key_moments:
        lines.append("## Key Moments")
        for moment in key_moments:
            speaker = "Matt" if moment["role"] == "user" else "Claude"
            text = moment["text"]
            annotation = moment.get("annotation", "")
            if annotation:
                lines.append(f'**{speaker}:** "{text}" — *{annotation}*')
            else:
                lines.append(f'**{speaker}:** "{text}"')
            lines.append("")

    # Notes for Future Me
    if notes:
        lines.append("## Notes for Future Me")
        for note in notes:
            if note.startswith("**"):
                lines.append(f"- {note}")
            else:
                lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines)


# ── Text cleaning utilities ──────────────────────────────────

def _clean_text(text: str) -> str:
    """Clean transcript text — remove system reminders, XML, artifacts."""
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    text = re.sub(r'<observation>.*?</observation>', '', text, flags=re.DOTALL)
    text = re.sub(r'PROGRESS SUMMARY[:\s].*?(?=\n\n|\Z)', '', text, flags=re.DOTALL)
    text = re.sub(r'^\s*\d+→\s?', '', text, flags=re.MULTILINE)
    # Extract Signal messages
    match = re.search(r'\[Signal from \w+[^\]]*\]\s*(.+)', text, re.DOTALL)
    if match:
        text = match.group(1)
    return text.strip()


def _extract_signal_text(text: str) -> str | None:
    """Extract the actual message from a Signal delivery."""
    match = re.search(r'\[Signal from \w+[^\]]*\]\s*(.+)', text, re.DOTALL)
    return match.group(1).strip() if match else None


def _looks_like_human_text(text: str) -> bool:
    """Check if a text block looks like it was typed by a human (not system/hook output).

    Filters out: file contents, system reminders, hook output, skill injections, tool artifacts.
    """
    # System/hook/skill artifacts
    if any(text.startswith(p) for p in [
        "You are", "Caveat:", "PROGRESS SUMMARY",
        "Claude's Full Response", "Full Response to User",
        "Called the", "Result of calling",
        "Base directory for this skill",
        "Available skills", "Note:", "Contents of",
    ]):
        return False
    # Lines that look like file content (line numbers, code)
    if text.count("→") > 2 or text.count("│") > 3:
        return False
    # Very long texts are probably pasted content, not typed messages
    if len(text) > 1500:
        return False
    # Blocks with lots of code-like patterns
    if text.count("```") > 1 or text.count("def ") > 2 or text.count("import ") > 3:
        return False
    return True
