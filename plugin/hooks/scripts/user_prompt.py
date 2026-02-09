#!/usr/bin/env python3
"""UserPromptSubmit hook for Memorable.

Captures user prompts to ~/.memorable/data/prompts/{machine_id}.jsonl.
"""

import json
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path.home() / ".memorable" / "data"


def get_machine_id() -> str:
    """Read machine_id from config, fall back to hostname."""
    config_path = Path.home() / ".memorable" / "config.json"
    try:
        if config_path.exists():
            with open(config_path) as f:
                cfg = json.load(f)
            mid = cfg.get("machine_id")
            if mid:
                return mid
    except Exception:
        pass
    return socket.gethostname()


def main():
    error_log_path = Path.home() / ".memorable" / "hook-errors.log"

    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
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
        prompt_text = re.sub(
            r'<system-reminder>.*?</system-reminder>',
            '', prompt_text, flags=re.DOTALL
        ).strip()

        if not prompt_text or len(prompt_text) < 5:
            return

        # Skip context recovery prompts
        if re.search(r'^\*\*Claude:\*\*', prompt_text, re.MULTILINE):
            return

        # Skip observer/watcher session prompts
        lower = prompt_text.lower()
        if any(lower.startswith(p) for p in [
            "i'm ready to observe", "i'll observe", "i'll monitor",
            "i need to observe", "i can see this is just",
            "i appreciate the detailed instructions",
        ]):
            return

        machine_id = get_machine_id()

        # Append to per-machine JSONL file
        prompts_dir = DATA_DIR / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        prompts_file = prompts_dir / f"{machine_id}.jsonl"

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session_id,
            "prompt": prompt_text[:5000],
            "chars": len(prompt_text),
        }

        with open(prompts_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    except Exception as e:
        try:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] user_prompt: ERROR: {e}\n")
        except:
            pass


if __name__ == "__main__":
    main()
