#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Loads the startup seed and injects it as context for Claude.
Output to stdout becomes part of Claude's context.
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db import MemorableDB
from server.config import Config


def main():
    # Read hook input from stdin
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    config = Config()
    db = MemorableDB(Path(config["db_path"]))

    parts = []

    # Sacred facts — always loaded
    sacred = db.get_sacred_facts()
    if sacred:
        facts = "; ".join(f"{f['name']}: {f['description']}" for f in sacred)
        parts.append(f"[Memorable] Sacred facts: {facts}")

    # Recent session notes
    seed_count = config.get("seed_session_count", 10)
    recent = db.get_recent_summaries(limit=seed_count)
    if recent:
        titles = ", ".join(f"{s['title']} ({s['date']})" for s in recent[:5])
        parts.append(f"[Memorable] Recent sessions: {titles}")

    # Stats for awareness
    stats = db.get_stats()
    if stats["sessions"] > 0:
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
        print("[Memorable] Fresh installation — no memory data yet. Use memorable_record_significant to start building the knowledge graph.")


if __name__ == "__main__":
    main()
