#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Tells Claude to read seed files and recent session notes.
Seeds are read by Claude directly; notes from the last 5 days
are injected inline so Claude doesn't have to parse raw JSONL.

Read order: matt.md > claude.md > recent notes > now.md
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
NOTES_DIR = DATA_DIR / "notes"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"


def _get_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


def _get_user_name() -> str:
    name = _get_config().get("user_name")
    if name:
        return name
    if SEEDS_DIR.exists():
        for path in sorted(SEEDS_DIR.glob("*.md")):
            if path.stem not in ("claude", "now"):
                return path.stem
    return ""


MAX_NOTES_CHARS = 8000


def _read_recent_notes(days: int = 5) -> str:
    """Read session notes from the last N days across all machines."""
    if not NOTES_DIR.exists():
        return ""

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries = []

    for jsonl_file in NOTES_DIR.glob("*.jsonl"):
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
        return ""

    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)

    parts = []
    total = 0
    for entry in entries:
        ts = entry.get("ts", "")[:16]
        machine = entry.get("machine", "")
        note = entry.get("note", "")
        if not note:
            continue

        header = f"### Session {ts}"
        if machine:
            header += f" ({machine})"
        block = f"{header}\n\n{note}"

        if total + len(block) > MAX_NOTES_CHARS:
            break
        parts.append(block)
        total += len(block)

    return "\n\n---\n\n".join(parts)


def main():
    error_log_path = Path.home() / ".memorable" / "hook-errors.log"

    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        user_name = _get_user_name()
        seeds_dir = str(SEEDS_DIR)

        files = []
        if user_name:
            user_path = SEEDS_DIR / f"{user_name}.md"
            if user_path.exists():
                files.append(str(user_path))
        claude_path = SEEDS_DIR / "claude.md"
        if claude_path.exists():
            files.append(str(claude_path))

        now_path = SEEDS_DIR / "now.md"

        lines = [
            "[Memorable] BEFORE RESPONDING, read these files in order:",
            "",
        ]

        for f in files:
            lines.append(f"1. Read {f}")

        # Inject recent session notes (last 5 days) inline
        notes = _read_recent_notes(days=5)
        if notes:
            lines.append("")
            lines.append("2. Recent session notes (last 5 days):")
            lines.append("")
            lines.append(notes)

        if now_path.exists():
            lines.append(f"3. Read and update {now_path} (rolling current-state snapshot)")
        else:
            lines.append(f"3. Create {now_path} (rolling current-state snapshot)")

        lines.append("")
        lines.append("Do NOT skip this. Do NOT respond before reading these files.")

        print("\n".join(lines))

    except Exception as e:
        try:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: CRITICAL ERROR: {e}\n")
            print(f"[Memorable] Error loading context (logged to {error_log_path})")
        except:
            print("[Memorable] Error loading context")


if __name__ == "__main__":
    main()
