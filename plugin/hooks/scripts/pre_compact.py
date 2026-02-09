#!/usr/bin/env python3
"""PreCompact hook for Memorable.

Before context compaction, point Claude to anchors and seeds for post-compaction recovery.
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
        seeds_dir = DATA_DIR / "seeds"

        lines = [
            "[Memorable] Context compaction incoming. After compaction, read these files to re-establish context:",
            "",
        ]

        if seeds_dir.exists():
            for md in sorted(seeds_dir.glob("*.md")):
                lines.append(f"1. Read {md}")

        if anchors_dir.exists():
            for af in sorted(anchors_dir.glob("*.md")):
                lines.append(f"2. Read {af}")

        lines.append("")
        lines.append("Do NOT skip this. These files contain who you are, what you were working on, and what was decided.")
        lines.append("Compare anchor timestamps against today's date to understand recency.")

        print("\n".join(lines))

    except Exception:
        pass


if __name__ == "__main__":
    main()
