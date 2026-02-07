#!/usr/bin/env python3
"""UserPromptSubmit hook for Memorable.

Captures user prompts with NLEmbedding for semantic search.
Raw text, no LLM processing — fast and reliable.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db import MemorableDB
from server.config import Config
from server.observer import embed_text


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    config = Config()
    if not config.get("observer_enabled", True):
        return

    session_id = hook_input.get("session_id", "")
    prompt_text = hook_input.get("prompt", "")

    if not session_id or not prompt_text:
        return

    # Skip very short prompts ("yes", "ok", "y", etc.)
    stripped = prompt_text.strip().lower()
    if len(stripped) < 5:
        return

    # Strip system-reminder blocks — they're noise
    import re
    prompt_text = re.sub(
        r'<system-reminder>.*?</system-reminder>',
        '', prompt_text, flags=re.DOTALL
    ).strip()

    if not prompt_text or len(prompt_text) < 5:
        return

    db = MemorableDB(
        Path(config["db_path"]),
        sync_url=config.get("sync_url", ""),
        auth_token=config.get("sync_auth_token", ""),
    )

    # Get next prompt number for this session
    prompt_number = db.get_prompt_count_for_session(session_id) + 1

    # Embed for semantic search
    embedding = embed_text(prompt_text[:500])

    db.store_user_prompt(
        session_id=session_id,
        prompt_number=prompt_number,
        prompt_text=prompt_text[:5000],  # cap at 5k chars
        embedding=embedding,
    )


if __name__ == "__main__":
    main()
