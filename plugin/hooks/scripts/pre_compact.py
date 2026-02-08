#!/usr/bin/env python3
"""PreCompact hook for Memorable.

Before context compaction, remind Claude to save important context.

Output to stdout is added to Claude's context before compaction.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db import MemorableDB
from server.config import Config


def main():
    error_log_path = Path.home() / ".memorable" / "hook-errors.log"

    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        # Output a reminder for Claude to preserve important context
        print(
            "[Memorable] Context compaction incoming. "
            "If there are important decisions, discoveries, or context from this session, "
            "use memorable_record_significant to save them to the knowledge graph."
        )

    except Exception as e:
        # Never crash — log error and return gracefully
        try:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] pre_compact: ERROR: {e}\n")
        except:
            # Even logging failed — silently pass
            pass


if __name__ == "__main__":
    main()
