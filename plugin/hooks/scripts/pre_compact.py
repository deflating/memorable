#!/usr/bin/env python3
"""PreCompact hook for Memorable.

Before context compaction, point Claude to anchors for post-compaction recovery.
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"


def main():
    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        anchors_dir = DATA_DIR / "anchors"
        lines = [
            "[Memorable] Context compaction incoming.",
            f"After compaction, re-establish context by reading anchors in {anchors_dir}/",
            "Also re-read seed files in ~/.memorable/data/seeds/ (all .md files there)",
        ]
        print("\n".join(lines))

    except Exception:
        pass


if __name__ == "__main__":
    main()
