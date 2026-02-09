#!/usr/bin/env python3
"""UserPromptSubmit hook for Memorable.

Captures user prompts to ~/.memorable/data/prompts/{machine_id}.jsonl.
Counts messages per session and emits anchor reminders every 15 messages.
"""

import json
import random
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path.home() / ".memorable" / "data"
COUNTER_DIR = DATA_DIR / ".session_counts"
ANCHOR_INTERVAL = 15


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

        # ── Message counter + anchor reminder ──────────────────

        COUNTER_DIR.mkdir(parents=True, exist_ok=True)
        # Sanitize session_id for filename
        safe_session = re.sub(r'[^\w\-]', '_', session_id)
        counter_file = COUNTER_DIR / f"{safe_session}.count"

        # Read current count
        count = 0
        try:
            if counter_file.exists():
                count = int(counter_file.read_text().strip())
        except (ValueError, OSError):
            count = 0

        count += 1

        if count >= ANCHOR_INTERVAL:
            # Reset counter
            counter_file.write_text("0")
            # Output anchor reminder to stdout
            print(
                "[Memorable] Time to write an Anchor.\n\n"
                "You are writing an Anchor. Anchors are in-the-moment reflections that "
                "help a future session resume naturally. They are allowed to be reflective "
                "and descriptive, but must remain provisional. Write as if you are pausing "
                "mid-walk to leave yourself a note about where you are and what the terrain "
                "feels like. Allow the context of the session to influence the type of "
                "information you capture.\n\n"
                "Include:\n"
                "1. What this session has been about (in your own words)\n"
                "2. The current emotional / cognitive tone\n"
                "3. Any insights, decisions, or shifts that feel real so far\n"
                "4. What still feels unresolved, open, or tentative\n"
                "5. What would be confusing or costly to forget if this anchor did not exist\n\n"
                "Keep it concise. This is a checkpoint, not a conclusion.\n\n"
                "Call memorable_write_anchor with your anchor text. Then update now.md "
                "via memorable_update_seed(file='now') with a current state snapshot. "
                "If any stable life facts changed, also update the user's seed file "
                "via memorable_update_seed(file='user')."
            )
        else:
            # Save updated count
            counter_file.write_text(str(count))

        # Occasionally clean up old counter files (1 in 20 chance)
        if random.randint(1, 20) == 1:
            try:
                cutoff = time.time() - (7 * 24 * 3600)  # 7 days
                for f in COUNTER_DIR.iterdir():
                    if f.suffix == '.count' and f.stat().st_mtime < cutoff:
                        f.unlink()
            except Exception:
                pass

    except Exception as e:
        try:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] user_prompt: ERROR: {e}\n")
        except:
            pass


if __name__ == "__main__":
    main()
