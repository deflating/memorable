#!/usr/bin/env python3
"""PostToolUse hook for Memorable.

Captures tool usage by appending to ~/.memorable/data/observations/{machine_id}.jsonl.
No database, no queue — just fast file append.
"""

import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path.home() / ".memorable" / "data"

SKIP_TOOLS = {
    "TodoWrite", "AskUserQuestion", "ListMcpResourcesTool",
    "ToolSearch", "EnterPlanMode", "ExitPlanMode",
}


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

        tool_name = hook_input.get("tool_name", "")
        if tool_name in SKIP_TOOLS:
            return

        session_id = hook_input.get("session_id", "unknown")
        tool_input = json.dumps(hook_input.get("tool_input", {}))
        tool_response = str(hook_input.get("tool_response", ""))

        # Truncate large payloads
        max_len = 3000
        if len(tool_response) > max_len:
            tool_response = tool_response[:max_len] + "\n[...truncated]"
        if len(tool_input) > max_len:
            tool_input = tool_input[:max_len] + "\n[...truncated]"

        # Extract file path from common tool inputs
        file_path = ""
        try:
            inp = hook_input.get("tool_input", {})
            file_path = (inp.get("file_path", "") or
                         inp.get("path", "") or
                         inp.get("pattern", ""))
        except Exception:
            pass

        # Build a short summary
        summary = f"{tool_name}"
        if file_path:
            summary += f" on {file_path}"

        machine_id = get_machine_id()

        # Append to per-machine JSONL file
        obs_dir = DATA_DIR / "observations"
        obs_dir.mkdir(parents=True, exist_ok=True)
        obs_file = obs_dir / f"{machine_id}.jsonl"

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session_id,
            "tool": tool_name,
            "file": file_path,
            "summary": summary,
            "input": tool_input[:500],
            "response_preview": tool_response[:200],
        }

        with open(obs_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    except Exception as e:
        try:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] post_tool_use: ERROR: {e}\n")
        except:
            pass


if __name__ == "__main__":
    main()
