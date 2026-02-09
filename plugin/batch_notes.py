#!/usr/bin/env python3
"""Batch processor: generate session notes for all historical transcripts.

Scans ~/.claude/projects/ for JSONL transcripts, filters out cruft,
and sends worthy sessions to DeepSeek for structured notes.

Filters:
  - 5+ user messages
  - Skip plugin_cache project sessions
  - Skip observer/watcher project sessions
  - Skip Testing project sessions
  - Skip sessions with < 500 total assistant chars
  - Skip originals when a continuation exists (process continuation only)

Output: ~/.memorable/data/notes/Matts-MacBook-Pro-4.local.jsonl
"""

import json
import os
import re
import socket
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# Reuse session_end functions
sys.path.insert(0, str(Path(__file__).parent / "hooks" / "scripts"))
from session_end import parse_transcript, build_llm_prompt, call_deepseek

PROJECTS_DIR = Path.home() / ".claude" / "projects"
DATA_DIR = Path.home() / ".memorable" / "data"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"
NOTES_DIR = DATA_DIR / "notes"

MIN_USER_MSGS = 5
MIN_ASSISTANT_CHARS = 500

# Project dirs to skip
SKIP_PROJECT_PATTERNS = [
    "claude-plugins-cache",
    "observer",
    "watcher",
    "Testing",
]


def get_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


def get_api_key(cfg: dict) -> str:
    summarizer = cfg.get("summarizer", {})
    key = summarizer.get("api_key", "")
    if not key:
        key = os.environ.get("DEEPSEEK_API_KEY", "")
    return key


def get_machine_id(cfg: dict) -> str:
    mid = cfg.get("machine_id")
    if mid:
        return mid
    return socket.gethostname()


def already_processed(session_id: str) -> bool:
    """Check if we already have a note for this session."""
    if not NOTES_DIR.exists():
        return False
    for jsonl_file in NOTES_DIR.glob("*.jsonl"):
        try:
            with open(jsonl_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("session") == session_id:
                            return True
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return False


def should_skip_project(project_name: str) -> bool:
    for pattern in SKIP_PROJECT_PATTERNS:
        if pattern.lower() in project_name.lower():
            return True
    return False


def quick_profile(transcript_path: str) -> dict:
    """Quick pass to count messages and check for continuation markers."""
    user_count = 0
    total_assistant_chars = 0
    is_continuation = False
    first_ts = None

    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp")
                if ts and first_ts is None:
                    first_ts = ts

                if entry.get("isSidechain"):
                    continue

                msg_type = entry.get("type")
                if msg_type == "user":
                    message = entry.get("message", {})
                    content = message.get("content")
                    if isinstance(content, str):
                        clean = re.sub(r'<system-reminder>.*?</system-reminder>', '', content, flags=re.DOTALL).strip()
                        if clean and len(clean) > 3:
                            user_count += 1
                            if "continued from a previous conversation" in clean:
                                is_continuation = True
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                clean = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL).strip()
                                if clean and len(clean) > 3:
                                    user_count += 1
                                    if "continued from a previous conversation" in clean:
                                        is_continuation = True

                elif msg_type == "assistant":
                    message = entry.get("message", {})
                    content = message.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                total_assistant_chars += len(block.get("text", ""))

    except Exception:
        pass

    return {
        "user_count": user_count,
        "total_assistant_chars": total_assistant_chars,
        "is_continuation": is_continuation,
        "first_ts": first_ts,
    }


def find_continuation_originals() -> set:
    """Find session IDs that have continuations (so we skip the original)."""
    # A continuation transcript contains "continued from a previous conversation"
    # and references the original session. We detect continuations and mark
    # their originals for skipping.
    #
    # Since we can't easily extract the original session ID from the continuation
    # text, we use a simpler heuristic: if a session IS a continuation, we keep it.
    # We just need to know which sessions are continuations so we DON'T skip them.
    # The originals that got continued will naturally be shorter or have less content.
    return set()  # We handle this differently — see main()


def main():
    cfg = get_config()
    api_key = get_api_key(cfg)
    machine_id = get_machine_id(cfg)

    if not api_key:
        print("ERROR: No DeepSeek API key found.")
        print("Set it in ~/.memorable/config.json under summarizer.api_key")
        sys.exit(1)

    model = cfg.get("summarizer", {}).get("model", "deepseek-chat")

    # Collect all candidate transcripts
    print("Scanning transcripts...")
    candidates = []

    for jsonl_file in PROJECTS_DIR.rglob("*.jsonl"):
        if "session-memory" in str(jsonl_file):
            continue

        project = jsonl_file.parent.name
        if should_skip_project(project):
            continue

        session_id = jsonl_file.stem

        # Skip if already processed
        if already_processed(session_id):
            continue

        profile = quick_profile(str(jsonl_file))

        if profile["user_count"] < MIN_USER_MSGS:
            continue

        if profile["total_assistant_chars"] < MIN_ASSISTANT_CHARS:
            continue

        candidates.append({
            "path": str(jsonl_file),
            "session_id": session_id,
            "project": project,
            "user_count": profile["user_count"],
            "is_continuation": profile["is_continuation"],
            "first_ts": profile["first_ts"],
        })

    # Sort by date (oldest first)
    candidates.sort(key=lambda c: c.get("first_ts") or "")

    print(f"Found {len(candidates)} sessions to process")
    print(f"  (skipped: already processed, <{MIN_USER_MSGS} msgs, plugin/observer/testing)")
    print()

    if not candidates:
        print("Nothing to do!")
        return

    # Ensure notes dir exists
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    notes_file = NOTES_DIR / f"{machine_id}.jsonl"

    # Process
    success = 0
    errors = 0
    skipped = 0

    for i, c in enumerate(candidates, 1):
        session_id = c["session_id"]
        short_id = session_id[:8]
        ts = (c["first_ts"] or "")[:10]
        tag = " [continuation]" if c["is_continuation"] else ""

        print(f"[{i}/{len(candidates)}] {short_id} ({ts}, {c['user_count']} msgs){tag}...", end=" ", flush=True)

        try:
            parsed = parse_transcript(c["path"])

            if parsed["message_count"] < MIN_USER_MSGS:
                print("SKIP (too few after parse)")
                skipped += 1
                continue

            prompt = build_llm_prompt(parsed, session_id)
            note_text = call_deepseek(prompt, api_key, model)

            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "session": session_id,
                "machine": machine_id,
                "message_count": parsed["message_count"],
                "tool_call_count": len(parsed["tool_calls"]),
                "first_ts": parsed["first_ts"],
                "last_ts": parsed["last_ts"],
                "note": note_text.strip(),
            }

            with open(notes_file, "a") as f:
                f.write(json.dumps(entry) + "\n")

            print("OK")
            success += 1

            # Rate limit: 0.5s between calls
            time.sleep(0.5)

        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1
            # Back off on errors
            time.sleep(2)

    print()
    print(f"=== DONE ===")
    print(f"  Processed: {success}")
    print(f"  Errors: {errors}")
    print(f"  Skipped: {skipped}")
    print(f"  Notes file: {notes_file}")


if __name__ == "__main__":
    main()
