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
            anchor_files = sorted(anchors_dir.glob("*.md"))
            for af in anchor_files:
                line_count = sum(1 for line in af.read_text(encoding="utf-8").splitlines() if line.strip())
                lines.append(f"2. Read the last 20 lines of {af} (use offset/limit). It has {line_count} lines. If you need more context, read the full file.")

        lines.append("")
        lines.append("Do NOT skip this. These files contain who you are, what you were working on, and what was decided.")

        print("\n".join(lines))

    except Exception:
        pass


if __name__ == "__main__":
    main()
