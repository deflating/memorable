#!/usr/bin/env python3
"""SessionEnd hook for Memorable.

Thin wrapper that reads hook input (session_id + transcript_path)
and delegates to note_generator for the actual work. Kept as a
fallback for machines where the daemon isn't running.
"""

import json
import sys
import time
from pathlib import Path

# Add daemon dir to path so we can import note_generator
DAEMON_DIR = Path(__file__).resolve().parent.parent.parent / "daemon"
sys.path.insert(0, str(DAEMON_DIR))

from note_generator import generate_note, log_error


def main():
    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            return

        session_id = hook_input.get("session_id", "unknown")
        transcript_path = hook_input.get("transcript_path", "")

        if not transcript_path or not Path(transcript_path).exists():
            log_error(f"No transcript found at: {transcript_path}")
            return

        generate_note(session_id, transcript_path)

    except Exception as e:
        log_error(f"ERROR: {e}")


if __name__ == "__main__":
    main()
