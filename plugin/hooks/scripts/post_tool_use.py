#!/usr/bin/env python3
"""PostToolUse hook for Memorable.

Queues tool usage for async observation generation.
Fast path: just write to DB queue, don't block on afm.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db import MemorableDB
from server.config import Config

SKIP_TOOLS = {
    "TodoWrite", "AskUserQuestion", "ListMcpResourcesTool",
    "ToolSearch", "EnterPlanMode", "ExitPlanMode",
}


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = hook_input.get("tool_name", "")
    if tool_name in SKIP_TOOLS:
        return

    config = Config()
    if not config.get("observer_enabled", True):
        return

    db = MemorableDB(
        Path(config["db_path"]),
        sync_url=config.get("sync_url", ""),
        auth_token=config.get("sync_auth_token", ""),
    )

    session_id = hook_input.get("session_id", "unknown")
    tool_input = json.dumps(hook_input.get("tool_input", {}))
    tool_response = str(hook_input.get("tool_response", ""))

    # Truncate immediately — don't store huge strings in queue
    max_len = config.get("observer_max_tool_output", 3000)
    if len(tool_response) > max_len:
        tool_response = tool_response[:max_len] + "\n[...truncated]"
    if len(tool_input) > max_len:
        tool_input = tool_input[:max_len] + "\n[...truncated]"

    context_before = (hook_input.get("last_assistant_message", "") or "")[:500]
    context_after = (hook_input.get("last_user_message", "") or "")[:500]
    cwd = hook_input.get("cwd", "")

    db.queue_observation(
        session_id=session_id,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_response=tool_response,
        context_before=context_before,
        context_after=context_after,
        cwd=cwd,
    )


if __name__ == "__main__":
    main()
