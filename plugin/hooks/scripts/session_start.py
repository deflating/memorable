#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Tells Claude to read seed files in order, then surfaces the most
salient session notes (ranked by effective salience with decay).
Read order: matt.md > claude.md > now.md
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"

# Salience constants (must match session_end.py)
DECAY_FACTOR = 0.97
MIN_SALIENCE = 0.05
MAX_SALIENT_NOTES = 8
MAX_SALIENT_CHARS = 6000


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


def _effective_salience(entry: dict) -> float:
    """Calculate effective salience for a note entry."""
    salience = entry.get("salience", 1.0)
    emotional_weight = entry.get("emotional_weight", 0.3)

    last_ref = entry.get("last_referenced", entry.get("ts", ""))
    try:
        ts_clean = str(last_ref).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except (ValueError, TypeError):
        days = 30

    adjusted_days = days * (1.0 - emotional_weight * 0.5)
    decayed = salience * (DECAY_FACTOR ** adjusted_days)
    return max(MIN_SALIENCE, decayed)


def _get_salient_notes(notes_dir: Path) -> list[tuple[float, dict]]:
    """Load all notes, score them, return sorted (highest salience first)."""
    scored = []
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
                    score = _effective_salience(entry)
                    scored.append((score, entry))
        except OSError:
            continue
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _format_salient_notes(scored: list[tuple[float, dict]]) -> str:
    """Format top notes as compact references (tags + date + session ID)."""
    parts = []
    for score, entry in scored[:MAX_SALIENT_NOTES]:
        tags = entry.get("topic_tags", [])
        tag_str = ", ".join(tags) if tags else "untagged"
        ts = entry.get("first_ts", entry.get("ts", ""))[:10]
        sid = entry.get("session", "")[:8]

        parts.append(f"  {ts} [{tag_str}] salience:{score:.2f} session:{sid}")

    return "\n".join(parts)


def main():
    error_log_path = Path.home() / ".memorable" / "hook-errors.log"

    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        user_name = _get_user_name()

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

        if now_path.exists():
            lines.append(f"2. Read {now_path} (current state + recent 5-day context)")
        else:
            lines.append(f"2. Create {now_path} (current state snapshot)")

        lines.append("")
        lines.append("Do NOT skip this. Do NOT respond before reading these files.")

        # Add salient session note references
        notes_dir = DATA_DIR / "notes"
        if notes_dir.exists():
            scored = _get_salient_notes(notes_dir)
            if scored:
                salient_text = _format_salient_notes(scored)
                lines.append("")
                lines.append(f"[Memorable] Most salient session notes ({len(scored)} total in {notes_dir}/):")
                lines.append(salient_text)
                lines.append(f"To read a note: grep {notes_dir}/ for its session ID. To search by topic: grep by keyword.")

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
