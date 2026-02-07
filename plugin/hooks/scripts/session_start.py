#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Loads context from the memory database and injects it for Claude.
Output to stdout becomes part of Claude's context before the first message.

Three layers:
1. Sacred facts — immutable truths (priority 10 KG entities)
2. Recent sessions — what's been happening the last few days (summaries + headers)
3. Recent activity — last observations and user prompts for continuity
"""

import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db import MemorableDB
from server.config import Config


def _format_session(s: dict) -> str:
    """Format a session for the seed — compact but informative."""
    header = s.get("header", "")

    line = f"  {s['date']} | {s['title'][:55]}"
    if header:
        line += f"\n    {header[:100]}"
    return line


def _format_observation(o: dict) -> str:
    """Format an observation — one line."""
    tool = o.get("tool_name", "?")
    title = o.get("title", "")[:70]
    files = ""
    if o.get("files"):
        try:
            file_list = json.loads(o["files"]) if isinstance(o["files"], str) else o["files"]
            if file_list:
                files = f" [{', '.join(str(f) for f in file_list[:2])}]"
        except (json.JSONDecodeError, TypeError):
            pass
    return f"  {tool:12s} {title}{files}"


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    config = Config()
    db = MemorableDB(Path(config["db_path"]))

    parts = []

    # ── Layer 1: Sacred facts ──────────────────────────────
    sacred = db.get_sacred_facts()
    if sacred:
        facts = "; ".join(f"{f['name']}: {f['description']}" for f in sacred)
        parts.append(f"[Memorable] Sacred facts: {facts}")

    # ── Layer 2: Recent sessions (last 5 days) ────────────
    recent = db.get_recent_sessions(days=5, limit=8)
    if recent:
        parts.append("[Memorable] Recent sessions (last 5 days):")
        for s in recent[:8]:
            parts.append(_format_session(s))

    # ── Layer 3: Recent activity (last ~30 observations) ──
    # Skip session_stop entries — they're just summaries of the above
    observations = db.get_recent_observations(limit=50)
    real_obs = [o for o in observations if o.get("observation_type") != "session_summary"][:20]

    if real_obs:
        parts.append("")
        parts.append("[Memorable] Recent activity:")
        for o in real_obs[:20]:
            parts.append(_format_observation(o))

    # ── Layer 4: Last few user prompts (continuity check) ──
    # These tell us what Matt was recently asking/thinking about
    try:
        prompts = db._query(lambda conn: [
            {"prompt_text": row[0], "created_at": row[1]}
            for row in conn.execute(
                "SELECT prompt_text, created_at FROM user_prompts "
                "ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
        ])
        # Only show if recent (last 2 hours)
        cutoff = time.time() - 7200
        recent_prompts = [p for p in prompts if (p.get("created_at") or 0) > cutoff]
        if recent_prompts:
            parts.append("")
            parts.append("[Memorable] Matt's recent messages (last 2h):")
            for p in recent_prompts[:5]:
                text = p["prompt_text"][:120].replace("\n", " ").strip()
                parts.append(f"  \"{text}\"")
    except Exception:
        pass  # prompts are nice-to-have, not critical

    # ── Stats ─────────────────────────────────────────────
    stats = db.get_stats()
    if stats["sessions"] > 0:
        parts.append("")
        parts.append(
            f"[Memorable] Memory: {stats['sessions']} sessions, "
            f"{stats['total_words_processed']:,} words processed, "
            f"{stats['kg_entities']} KG entities, "
            f"{stats['sacred_facts']} sacred facts. "
            f"Use memorable_search_sessions to search, memorable_get_startup_seed for full context."
        )

    if parts:
        print("\n".join(parts))
    else:
        print("[Memorable] Fresh installation — no memory data yet.")


if __name__ == "__main__":
    main()
