#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Tells Claude to read seed files and anchors. Does NOT inject content —
Claude reads the files itself so it actually engages with them.

Read order: matt.md > claude.md > anchors > now.md (read and update)
"""

import json
import sys
import time
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
ANCHORS_DIR = DATA_DIR / "anchors"
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


def main():
    error_log_path = Path.home() / ".memorable" / "hook-errors.log"

    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        user_name = _get_user_name()
        seeds_dir = str(SEEDS_DIR)
        anchors_dir = str(ANCHORS_DIR)

        files = []
        if user_name:
            user_path = SEEDS_DIR / f"{user_name}.md"
            if user_path.exists():
                files.append(str(user_path))
        claude_path = SEEDS_DIR / "claude.md"
        if claude_path.exists():
            files.append(str(claude_path))

        # List anchor files
        anchor_files = []
        if ANCHORS_DIR.exists():
            anchor_files = sorted(ANCHORS_DIR.glob("*.jsonl"))

        now_path = SEEDS_DIR / "now.md"

        lines = [
            "[Memorable] BEFORE RESPONDING, read these files in order:",
            "",
        ]

        for f in files:
            lines.append(f"1. Read {f}")

        if anchor_files:
            for af in anchor_files:
                lines.append(f"2. Read {af} (recent anchors — session summaries)")

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
