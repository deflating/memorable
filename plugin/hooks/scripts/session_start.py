#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Tells Claude to read seed files in order.
Read order: matt.md > claude.md > now.md
"""

import json
import sys
import time
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
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
