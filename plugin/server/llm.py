"""LLM interface for Memorable.

Calls Claude via the `claude` CLI (--print mode).
Uses the same subscription as Claude Code — no separate API key needed.

To swap in a local MLX model later, just change `call_llm()`.
"""

import json
import logging
import re
import subprocess
import shutil

logger = logging.getLogger(__name__)


def _find_claude() -> str | None:
    """Find the claude CLI executable."""
    return shutil.which("claude")


def call_llm(prompt: str, system: str = "",
             model: str = "sonnet") -> str:
    """Call a Claude model via the claude CLI in --print mode.

    Args:
        prompt: The user prompt to send.
        system: Optional system prompt.
        model: Model to use — "sonnet" (default), "haiku", or "opus".

    Returns:
        The model's text response, or "" on error.
    """
    claude_path = _find_claude()
    if not claude_path:
        logger.warning("claude CLI not found in PATH")
        return ""

    cmd = [
        claude_path,
        "-p",
        "--model", model,
        "--no-session-persistence",
        "--output-format", "text",
        "--system-prompt", system or "You are a helpful assistant. Respond concisely.",
    ]

    cmd.append(prompt)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            cwd="/tmp",  # avoid picking up CLAUDE.md from home dir
            env={**__import__("os").environ, "TOKENIZERS_PARALLELISM": "false"},
        )
        output = result.stdout.strip()
        if result.returncode != 0 and not output:
            stderr = result.stderr.strip()
            logger.warning(f"claude CLI error: {stderr[:200]}")
            return ""
        return output
    except subprocess.TimeoutExpired:
        logger.warning("claude CLI timed out")
        return ""
    except Exception as e:
        logger.error(f"LLM call error: {e}")
        return ""


def call_llm_json(prompt: str, system: str = "",
                   model: str = "sonnet") -> dict | None:
    """Call a Claude model and parse the response as JSON.

    Strips markdown fences if present. Returns None on parse failure.
    """
    raw = call_llm(prompt, system=system, model=model)
    if not raw:
        return None

    text = raw.strip()
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"JSON parse failed: {text[:200]}")
        return None
