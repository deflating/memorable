#!/usr/bin/env python3
"""PreCompact hook for Memorable.

Before context compaction, save a context seed summarizing
the current conversation state. This acts as a checkpoint.

Output to stdout is added to Claude's context before compaction.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db import MemorableDB
from server.config import Config


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    session_id = hook_input.get("session_id", "unknown")

    # Output a reminder for Claude to preserve important context
    print(
        "[Memorable] Context compaction incoming. "
        "If there are important decisions, discoveries, or context from this session, "
        "use memorable_record_significant to save them before they're compressed."
    )


if __name__ == "__main__":
    main()
