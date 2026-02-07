#!/usr/bin/env python3
"""Stop hook for Memorable.

When Claude finishes responding, this fires. We use it to
capture session context that can be loaded next time.

For Phase 1, this just logs. Phase 2 will use local Ollama
to generate a context seed from the conversation so far.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    # Phase 1: no-op (context seeds come from transcript processing)
    # Phase 2: will generate live context seed via Ollama here


if __name__ == "__main__":
    main()
