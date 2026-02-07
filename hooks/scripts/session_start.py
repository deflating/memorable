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

    # Last context seed — session continuation
    last_seed = db.get_last_context_seed()
    if last_seed:
        parts.append(f"[Memorable] Last session: {last_seed['seed_content']}")

    # Recent high-continuity sessions as pointers
    recent = db.get_recent_sessions(days=3, limit=5)
    if recent:
        session_list = ", ".join(
            f"{s['title']} ({s['date']}, c:{s['continuity']})"
            for s in recent if s['continuity'] >= 6
        )
        if session_list:
            parts.append(f"[Memorable] Recent important sessions: {session_list}")

    # Stats for awareness
    stats = db.get_stats()
    if stats["sessions"] > 0:
        parts.append(
            f"[Memorable] Memory: {stats['sessions']} sessions, "
            f"{stats['kg_entities']} KG entities, "
            f"{stats['sacred_facts']} sacred facts. "
            f"Use memorable_search_sessions to search, memorable_query_kg for knowledge graph."
        )

    if parts:
        print("\n".join(parts))
    else:
        print("[Memorable] Fresh installation — no memory data yet. Use memorable_record_significant to start building the knowledge graph.")


if __name__ == "__main__":
    main()
