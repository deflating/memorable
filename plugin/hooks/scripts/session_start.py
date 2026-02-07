#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Loads context from the memory database and injects it for Claude.
Output to stdout becomes part of Claude's context before the first message.

Three layers:
1. Rolling summary — 5-day synthesis of what's been happening
2. Recent sessions — titles + headers for the last few days (dig deeper if needed)
3. Timeline — last 2h of prompts + observations interleaved chronologically
   (like conversation.md for Signal Claude — the raw thread of what happened)

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
from server.summaries import get_or_generate_summary


def _format_session(s: dict) -> str:
    """Format a session for the seed — compact but informative."""
    header = s.get("header", "")

    line = f"  {s['date']} | {s['title'][:55]}"
    if header:
        line += f"\n    {header[:100]}"
    return line


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    config = Config()
    db = MemorableDB(Path(config["db_path"]))

    parts = []

    # ── Layer 1: Rolling summary (5-day synthesis) ────────
    try:
        rolling = get_or_generate_summary(config, db)
        if rolling:
            parts.append("[Memorable] Rolling summary (last 5 days):")
            if len(rolling) > 800:
                rolling = rolling[:797] + "..."
            parts.append(f"  {rolling}")
    except Exception:
        pass

    # ── Layer 2: Recent sessions (last 5 days) ────────────
    recent = db.get_recent_sessions(days=5, limit=8)
    if recent:
        parts.append("")
        parts.append("[Memorable] Recent sessions (last 5 days):")
        for s in recent[:8]:
            parts.append(_format_session(s))

    # ── Layer 3: Timeline (last 2h — prompts + observations interleaved) ──
    # Like conversation.md for Signal Claude: the raw thread of what happened.
    try:
        cutoff = time.time() - 7200
        timeline = db.get_timeline(limit=100)
        recent_timeline = [t for t in timeline if (t.get("created_at") or 0) > cutoff]
        # Timeline comes newest-first; reverse for chronological
        recent_timeline.reverse()

        if recent_timeline:
            parts.append("")
            parts.append("[Memorable] Recent timeline (last 2h):")
            for item in recent_timeline[:40]:
                if item["kind"] == "prompt":
                    text = (item.get("prompt_text") or "")[:120].replace("\n", " ").strip()
                    parts.append(f'  Matt: "{text}"')
                else:
                    tool = item.get("tool_name") or "?"
                    title = (item.get("title") or "")[:60]
                    parts.append(f"  {tool}: {title}")
    except Exception:
        pass

    # ── Stats (compact) ───────────────────────────────────
    stats = db.get_stats()
    if stats["sessions"] > 0:
        parts.append("")
        parts.append(
            f"[Memorable] {stats['sessions']} sessions, "
            f"{stats['total_words_processed']:,} words processed, "
            f"{stats['kg_entities']} KG entities. "
            f"Use memorable_search_sessions to search."
        )

    if parts:
        print("\n".join(parts))
    else:
        print("[Memorable] Fresh installation — no memory data yet.")


if __name__ == "__main__":
    main()
