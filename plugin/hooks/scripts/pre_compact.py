#!/usr/bin/env python3
"""PreCompact hook for Memorable.

Before context compaction, remind Claude that session data is captured automatically.
"""

import json
import sys


def main():
    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        print(
            "[Memorable] Context compaction incoming. "
            "Session data is captured automatically — no action needed."
        )

    except Exception:
        pass


if __name__ == "__main__":
    main()
