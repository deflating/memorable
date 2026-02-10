#!/usr/bin/env python3
"""PreCompact hook for Memorable.

Before context compaction, point Claude to seeds and conversation transcript
for post-compaction recovery.
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

        transcripts_dir = DATA_DIR / "transcripts"
        seeds_dir = DATA_DIR / "seeds"

        lines = [
            "[Memorable] Context compaction incoming. After compaction, read these files to re-establish context:",
            "",
        ]

        if seeds_dir.exists():
            for md in sorted(seeds_dir.glob("*.md")):
                lines.append(f"1. Read {md}")

        if transcripts_dir.exists():
            for tf in sorted(transcripts_dir.glob("*.md")):
                line_count = sum(1 for line in tf.read_text(encoding="utf-8").splitlines() if line.strip())
                lines.append(f"2. Read the last 100 lines of {tf} (use offset/limit). It has {line_count} lines. If you need more context, read further back.")

        lines.append("")
        lines.append("Do NOT skip this. These files contain who you are and what you were working on.")

        print("\n".join(lines))

    except Exception:
        pass


if __name__ == "__main__":
    main()
