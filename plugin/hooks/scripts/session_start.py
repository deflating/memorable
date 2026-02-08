#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Loads context from the memory database and injects it for Claude.
Output to stdout becomes part of Claude's context before the first message.

Three layers:
1. Rolling summary — 5-day synthesis of what's been happening
2. Recent sessions — WITH their full narrative summaries and unfinished work
3. Stats — one-line system status

CLAUDE.md reading is handled separately by the SessionStart hook in settings.json.
"""

import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db import MemorableDB
from server.config import Config


def _format_session_note(s: dict) -> str:
    """Format a session note with its full narrative summary."""
    parts = []
    parts.append(f"\n### {s['date']} — {s['title']}")

    # Show mood if present
    mood = s.get("mood", "").strip()
    if mood:
        parts.append(f"**Mood:** {mood}")

    # Show the full note content (the structured summary from notes.py)
    note_content = s.get("note_content", "").strip()
    if note_content:
        parts.append(note_content)
    elif s.get("compressed_50", "").strip():
        # Fallback to compressed_50 if note_content isn't available
        parts.append(s["compressed_50"])

    return "\n".join(parts)


def _extract_unfinished_work(note_content: str) -> list[str]:
    """Extract unfinished work bullets from a session note."""
    if not note_content:
        return []

    unfinished = []
    in_unfinished_section = False

    for line in note_content.split("\n"):
        if "**Unfinished:**" in line or "## Unfinished" in line:
            in_unfinished_section = True
            continue

        if in_unfinished_section:
            # Stop at next section header
            if line.startswith("##") or line.startswith("**") and line.endswith("**"):
                break

            # Capture bullet points
            stripped = line.strip()
            if stripped.startswith("-") or stripped.startswith("*"):
                unfinished.append(stripped[1:].strip())

    return unfinished


def main():
    error_log_path = Path.home() / ".memorable" / "hook-errors.log"

    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        config = Config()
        db = MemorableDB(Path(config["db_path"]))

        parts = []

        # ── Layer 1: Rolling summary (5-day synthesis) — NO TRUNCATION ────────
        # Don't generate if not cached (would block startup for 5-15s)
        try:
            # Check if we have a cached rolling summary
            summary = db.get_latest_rolling_summary()
            if summary:
                rolling = summary.get("content", "")
                if rolling:
                    parts.append("[Memorable] Rolling summary (last 5 days):")
                    parts.append(f"  {rolling}")
        except Exception as e:
            # Log error but don't crash
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: rolling summary error: {e}\n")

        # ── Layer 2: Recent sessions WITH full narrative summaries (last 3 sessions) ──
        try:
            # Fetch sessions with all fields including note_content
            def get_sessions_with_notes(conn):
                cutoff = time.time() - (5 * 86400)  # last 5 days
                cur = conn.execute(
                    """SELECT transcript_id, date, title, note_content, mood,
                              compressed_50, header
                       FROM sessions
                       WHERE created_at > ?
                       ORDER BY date DESC
                       LIMIT 3""",
                    (cutoff,)
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

            recent = db._query(get_sessions_with_notes)

            if recent:
                parts.append("")
                parts.append("[Memorable] Recent sessions:")

                # Check for unfinished work in the most recent session
                if recent and recent[0].get("note_content"):
                    unfinished = _extract_unfinished_work(recent[0]["note_content"])
                    if unfinished:
                        parts.append("")
                        parts.append("**⚠️ Unfinished work from last session:**")
                        for item in unfinished:
                            parts.append(f"  - {item}")
                        parts.append("")

                # Show full session notes
                for s in recent:
                    parts.append(_format_session_note(s))
        except Exception as e:
            # Log error but don't crash
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: recent sessions error: {e}\n")

        # ── Stats (one line) ───────────────────────────────────
        try:
            stats = db.get_stats()
            if stats["sessions"] > 0:
                parts.append("")
                parts.append(
                    f"[Memorable] {stats['sessions']} sessions | "
                    f"{stats['total_words_processed']:,} words | "
                    f"{stats['kg_entities']} entities | "
                    f"{stats.get('observations', 0)} observations | "
                    f"Use memorable_search_sessions to search."
                )
        except Exception as e:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: stats error: {e}\n")

        if parts:
            print("\n".join(parts))
        else:
            print("[Memorable] Fresh installation — no memory data yet.")

    except Exception as e:
        # TOP-LEVEL ERROR HANDLER: Never crash, always return gracefully
        try:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: CRITICAL ERROR: {e}\n")
            print(f"[Memorable] Error loading context (logged to {error_log_path})")
        except:
            # Even logging failed — just output a minimal message
            print("[Memorable] Error loading context")


if __name__ == "__main__":
    main()
