#!/usr/bin/env python3
"""Stop hook for Memorable.

When a Claude session ends, generates a session-level summary
observation from all the individual observations captured during
the session. Uses Apple Foundation Model (afm) for summarization
and Apple NLEmbedding for the embedding.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db import MemorableDB
from server.config import Config
from server.observer import generate_session_summary, embed_text


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    session_id = hook_input.get("session_id", "")
    if not session_id:
        return

    config = Config()
    if not config.get("observer_enabled", True):
        return

    db = MemorableDB(
        Path(config["db_path"]),
        sync_url=config.get("sync_url", ""),
        auth_token=config.get("sync_auth_token", ""),
    )

    # Get all observations for this session
    observations = db.get_observations_by_session(session_id)
    if len(observations) < 2:
        return  # not enough to summarize

    # Generate session summary via afm
    summary = generate_session_summary(observations, session_id)
    if not summary:
        return

    # Embed and store
    embed_str = f"{summary['title']}. {summary['summary']}"
    embedding = embed_text(embed_str)

    db.store_observation(
        session_id=session_id,
        obs_type="session_summary",
        title=summary["title"],
        summary=summary["summary"],
        files=json.dumps(summary.get("files", [])),
        embedding=embedding,
        tool_name="session_stop",
    )


if __name__ == "__main__":
    main()
