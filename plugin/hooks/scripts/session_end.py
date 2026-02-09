#!/usr/bin/env python3
"""SessionEnd hook for Memorable.

Reads the full session transcript, extracts meaningful content,
sends it to an LLM for structured session notes, and writes them
to ~/.memorable/data/notes/{machine_id}.jsonl.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = Path.home() / ".memorable" / "data"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"
ERROR_LOG = Path.home() / ".memorable" / "hook-errors.log"

# Default summarizer config
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-chat"

# Max chars of transcript to send to the LLM
MAX_TRANSCRIPT_CHARS = 80_000


def log_error(msg: str):
    try:
        with open(ERROR_LOG, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_end: {msg}\n")
    except Exception:
        pass


def get_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


def get_machine_id(cfg: dict) -> str:
    mid = cfg.get("machine_id")
    if mid:
        return mid
    return socket.gethostname()


def parse_transcript(transcript_path: str) -> dict:
    """Parse a Claude Code JSONL transcript into structured content.

    Returns dict with:
      - messages: list of {"role": "user"|"assistant", "text": str} in order
      - tool_calls: list of {tool, target} dicts
      - message_count: int (user messages only)
      - first_ts / last_ts: timestamps
    """
    import re

    messages = []
    tool_calls = []
    first_ts = None
    last_ts = None

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                msg_type = entry.get("type")
                if entry.get("isSidechain"):
                    continue

                if msg_type == "user":
                    message = entry.get("message", {})
                    content = message.get("content")
                    texts = []
                    if isinstance(content, str):
                        texts.append(content)
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                texts.append(block.get("text", ""))
                    for text in texts:
                        clean = re.sub(
                            r'<system-reminder>.*?</system-reminder>',
                            '', text, flags=re.DOTALL
                        ).strip()
                        if clean and len(clean) > 3:
                            messages.append({"role": "user", "text": clean[:2000]})

                elif msg_type == "assistant":
                    message = entry.get("message", {})
                    content = message.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "text":
                                text = block.get("text", "")
                                if text and len(text) > 10:
                                    messages.append({"role": "assistant", "text": text[:3000]})
                            elif block.get("type") == "tool_use":
                                tool_name = block.get("name", "")
                                tool_input = block.get("input", {})
                                target = (
                                    tool_input.get("file_path", "") or
                                    tool_input.get("path", "") or
                                    tool_input.get("pattern", "") or
                                    tool_input.get("command", "")
                                )
                                tool_calls.append({
                                    "tool": tool_name,
                                    "target": str(target)[:200],
                                })

    except Exception as e:
        log_error(f"parse_transcript error: {e}")

    user_count = sum(1 for m in messages if m["role"] == "user")
    return {
        "messages": messages,
        "tool_calls": tool_calls,
        "message_count": user_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def build_llm_prompt(parsed: dict, session_id: str) -> str:
    """Build a prompt for the LLM to generate session notes."""

    # Build interleaved transcript
    parts = []
    parts.append("# Session Transcript\n")

    for msg in parsed["messages"]:
        role = "Matt" if msg["role"] == "user" else "Claude"
        text = msg["text"]
        if msg["role"] == "assistant" and len(text) > 500:
            text = text[:500] + "..."
        parts.append(f"**{role}:** {text}\n")

    # Notable tool calls
    notable_tools = [t for t in parsed["tool_calls"]
                     if t["tool"] in ("Edit", "Write", "Bash", "NotebookEdit",
                                      "mcp__deepseek__chat_completion",
                                      "mcp__deepseek__multi_turn_chat")]
    if notable_tools:
        parts.append("\n## Notable Tool Calls")
        for t in notable_tools[:30]:
            parts.append(f"- {t['tool']}: {t['target']}")

    transcript_text = "\n".join(parts)

    if len(transcript_text) > MAX_TRANSCRIPT_CHARS:
        transcript_text = transcript_text[:MAX_TRANSCRIPT_CHARS] + "\n\n[...truncated]"

    prompt = f"""You are a session note-taker for an AI coding assistant (Claude Code). You will receive a raw session transcript. Write structured session notes that capture both technical work and human context.

{transcript_text}

---

Output format (use only the sections that apply — skip empty ones):

## Summary
One paragraph. What happened in this session, in plain language.

## Decisions
Choices that were made and why. Format: "Chose X over Y — reason." These are high-value because they prevent the AI from re-suggesting rejected approaches.

## Rejections
Things that were explicitly tried or considered and didn't work or were abandoned. Include why if stated.

## Technical Context
- Project conventions discovered (test framework, file structure, naming patterns)
- Dependencies added or removed
- Bugs fixed (what was wrong, what fixed it)
- Files significantly modified
- Commands or workflows established

## User Preferences
Anything the user expressed a preference about — coding style, tools, approaches, communication style. Only include if explicitly stated or clearly demonstrated, not inferred.

## People & Life
Anyone mentioned by name and in what context. Life events, plans, situations discussed. This section exists because the AI is sometimes used as a companion, not just a coding tool — personal context matters for continuity.

## Mood
One or two words for the emotional register of the session (e.g. focused, frustrated, playful, low, excited). Then one sentence of context if relevant.

## Open Threads
Things left unfinished, unresolved, or explicitly marked for later. Questions that were raised but not answered. Plans stated but not executed.

---

Rules:
- Be concise. Each bullet should be one line.
- Use the user's actual words where possible, especially for decisions and rejections.
- Don't editorialize or add interpretation. Just capture what happened.
- Don't summarize tool calls or file reads unless they led to something significant.
- If the session is purely technical with no personal content, skip People & Life and keep Mood brief.
- If the session is purely conversational with no code, skip Technical Context.
- The notes will be read by an AI at the start of a future session to establish context, so optimise for that use case."""

    return prompt


def call_deepseek(prompt: str, api_key: str, model: str = "deepseek-chat") -> str:
    """Call DeepSeek API directly via HTTP."""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def call_gemini(prompt: str, api_key: str, model: str = "gemini-2.5-flash") -> str:
    """Call Gemini API directly via HTTP."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2000},
    }).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def call_claude(prompt: str, api_key: str, model: str = "claude-haiku-4-5-20251001") -> str:
    """Call Claude API directly via HTTP."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body = json.dumps({
        "model": model,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return data["content"][0]["text"]


def call_llm(prompt: str, cfg: dict) -> str:
    """Call the configured LLM provider."""
    summarizer = cfg.get("summarizer", {})
    provider = summarizer.get("provider", DEFAULT_PROVIDER)
    model = summarizer.get("model")
    api_key = summarizer.get("api_key", "")

    # Fall back to env vars if no key in config
    if not api_key:
        if provider == "deepseek":
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        elif provider == "gemini":
            api_key = os.environ.get("GOOGLE_AI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
        elif provider == "claude":
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        raise ValueError(f"No API key found for provider '{provider}'. "
                         f"Set it in ~/.memorable/config.json under summarizer.api_key "
                         f"or as an environment variable.")

    if provider == "deepseek":
        return call_deepseek(prompt, api_key, model or "deepseek-chat")
    elif provider == "gemini":
        return call_gemini(prompt, api_key, model or "gemini-2.5-flash")
    elif provider == "claude":
        return call_claude(prompt, api_key, model or "claude-haiku-4-5-20251001")
    else:
        raise ValueError(f"Unknown summarizer provider: {provider}")


ROLLING_SUMMARY_PROMPT = """You are writing a "now" document for an AI coding assistant (Claude Code). This document is read at the start of every new session so Claude can quickly orient: where things are right now, and how they got here.

You will receive session notes from the last 5 days. Synthesise them into a single concise document.

Output this exact markdown structure:

# Now

*Last updated: {date}*

## Active Focus
One sentence: what is Matt primarily working on right now?

## Current State
Bullet list of what exists, what's running, what's built. Be specific — file names, model names, port numbers, statuses. Max 8 bullets.

## Last 5 Days
2-3 sentences covering the main arc of recent work. What changed, what was built, what shifted.

## Recent Decisions
The most important choices made (max 5). Format: "Chose X over Y — reason."

## Open Threads
Things left unresolved or explicitly marked for later (max 5).

## People Mentioned Recently
Anyone mentioned in the last few days and why (max 5, one line each). Skip Claude and Matt.

## Mood
One sentence on how things have been feeling.

Rules:
- Keep the whole document under 1500 words.
- Be concrete and specific, not vague.
- Prioritise the most recent session heavily — that's the freshest context.
- Use Matt's actual words where possible.
- Don't include session timestamps or machine names.
"""


def generate_rolling_summary(cfg: dict, notes_dir: Path):
    """Read last 5 days of notes and generate a rolling summary via DeepSeek."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=5)
    entries = []

    for jsonl_file in notes_dir.glob("*.jsonl"):
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
                    if not ts:
                        continue
                    try:
                        ts_clean = str(ts).replace("Z", "+00:00")
                        dt = datetime.fromisoformat(ts_clean)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff:
                            continue
                    except (ValueError, TypeError):
                        continue
                    entries.append(entry)
        except OSError:
            continue

    if not entries:
        log_error("Rolling summary: no recent notes found")
        return

    # Sort newest first
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)

    # Build input: concat notes, cap at 30K chars
    parts = []
    total = 0
    for entry in entries:
        note = entry.get("note", "")
        if not note:
            continue
        if total + len(note) > 30_000:
            break
        parts.append(note)
        total += len(note)

    notes_text = "\n\n---\n\n".join(parts)
    today = datetime.now().strftime("%Y-%m-%d")
    prompt_text = ROLLING_SUMMARY_PROMPT.replace("{date}", today)
    prompt_text += "\n\nHere are the session notes:\n\n" + notes_text

    summary = call_llm(prompt_text, cfg)

    # Write to seeds/now.md (replaces the old now.md entirely)
    now_path = DATA_DIR / "seeds" / "now.md"
    now_path.parent.mkdir(parents=True, exist_ok=True)
    now_path.write_text(summary.strip() + "\n")

    # Clean up old recent.md if it exists
    recent_path = DATA_DIR / "seeds" / "recent.md"
    if recent_path.exists():
        recent_path.unlink()

    log_error(f"SUCCESS: now.md written ({len(summary)} chars, {len(entries)} notes)")


def main():
    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            return

        session_id = hook_input.get("session_id", "unknown")
        transcript_path = hook_input.get("transcript_path", "")

        if not transcript_path or not Path(transcript_path).exists():
            log_error(f"No transcript found at: {transcript_path}")
            return

        cfg = get_config()
        machine_id = get_machine_id(cfg)

        # Check if summarizer is configured
        summarizer = cfg.get("summarizer", {})
        if not summarizer.get("enabled", True):
            log_error("Summarizer disabled in config")
            return

        # Parse the transcript
        parsed = parse_transcript(transcript_path)

        # Skip very short sessions (< 3 user messages)
        if parsed["message_count"] < 3:
            log_error(f"Session too short ({parsed['message_count']} messages), skipping summary")
            return

        # Build LLM prompt and call
        prompt = build_llm_prompt(parsed, session_id)
        raw_response = call_llm(prompt, cfg)

        # Store the raw markdown note
        note_text = raw_response.strip()

        # Build the full entry
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session_id,
            "machine": machine_id,
            "message_count": parsed["message_count"],
            "tool_call_count": len(parsed["tool_calls"]),
            "first_ts": parsed["first_ts"],
            "last_ts": parsed["last_ts"],
            "note": note_text,
        }

        # Write to notes JSONL
        notes_dir = DATA_DIR / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        notes_file = notes_dir / f"{machine_id}.jsonl"

        with open(notes_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        log_error(f"SUCCESS: Note written for session {session_id} ({parsed['message_count']} msgs)")

        # Regenerate rolling 5-day summary
        try:
            generate_rolling_summary(cfg, notes_dir)
        except Exception as e:
            log_error(f"Rolling summary failed (non-fatal): {e}")

        # Rebuild oracle context and warm KV cache (runs in background)
        try:
            subprocess.Popen(
                [sys.executable, "-m", "oracle.build_context", "--warm"],
                cwd=str(PLUGIN_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log_error("Oracle context rebuild started (background)")
        except Exception as e:
            log_error(f"Oracle rebuild failed (non-fatal): {e}")

    except Exception as e:
        log_error(f"ERROR: {e}")


if __name__ == "__main__":
    main()
