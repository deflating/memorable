#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Reads session JSON files directly from ~/.memorable/data/sessions/
and injects context for Claude. No database required for startup.

Two layers:
1. Recent sessions — last 3 sessions with full summaries
2. Stats — one-line system status
"""

import json
import sys
import time
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"
SESSIONS_DIR = DATA_DIR / "sessions"


def _read_recent_sessions(limit: int = 3) -> list[dict]:
    """Read recent session JSON files, sorted by date descending."""
    if not SESSIONS_DIR.exists():
        return []
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
        try:
            sessions.append(json.loads(f.read_text()))
        except Exception:
            continue
        if len(sessions) >= limit:
            break
    return sessions


def _count_session_files() -> int:
    if not SESSIONS_DIR.exists():
        return 0
    return len(list(SESSIONS_DIR.glob("*.json")))


def _read_sacred_facts() -> list[dict]:
    sacred_file = DATA_DIR / "sacred.json"
    if not sacred_file.exists():
        return []
    try:
        data = json.loads(sacred_file.read_text())
        return data.get("facts", [])
    except Exception:
        return []


def main():
    error_log_path = Path.home() / ".memorable" / "hook-errors.log"

    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        parts = []

        # ── Recent sessions (last 3, from files) ──────────────
        try:
            recent = _read_recent_sessions(limit=3)
            if recent:
                parts.append("[Memorable] Recent sessions:")
                for s in recent:
                    parts.append(f"\n### {s['date']} — {s['title']}")
                    if s.get("summary"):
                        parts.append(s["summary"])
        except Exception as e:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: sessions error: {e}\n")

        # ── Sacred facts ──────────────────────────────────────
        try:
            facts = _read_sacred_facts()
            if facts:
                parts.append("")
                parts.append("[Memorable] Sacred facts:")
                for fact in facts:
                    parts.append(f"  - {fact['name']}: {fact.get('description', '')}")
        except Exception:
            pass

        # ── Stats ─────────────────────────────────────────────
        try:
            session_count = _count_session_files()
            if session_count > 0:
                parts.append("")
                parts.append(
                    f"[Memorable] Memory: {session_count} sessions. "
                    f"Use memorable_search_sessions to search."
                )
        except Exception:
            pass

        if parts:
            print("\n".join(parts))
        else:
            print("[Memorable] Fresh installation — no memory data yet.")

    except Exception as e:
        try:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: CRITICAL ERROR: {e}\n")
            print(f"[Memorable] Error loading context (logged to {error_log_path})")
        except:
            print("[Memorable] Error loading context")


if __name__ == "__main__":
    main()
