#!/usr/bin/env python3
"""Memorable background daemon.

Watches Claude Code session transcripts in real-time and generates
mid-session anchors via Apple Foundation Model (AFM). Anchors are
structured checkpoints for crash recovery — if a session crashes or
context is lost, the next session can read anchors to re-establish
where things were.

Usage:
    python3 memorable_daemon.py
    python3 memorable_daemon.py --no-anchors   # watch only, no AFM calls
"""

import argparse
import json
import logging
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from transcript_watcher import watch_transcripts

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"
ANCHORS_DIR = DATA_DIR / "anchors"

MACHINE_ID = socket.gethostname()

AFM_BIN = shutil.which("afm") or "/opt/homebrew/bin/afm"

AFM_SYSTEM = "You are a conversation summarizer. Be concise."

# Multi-pass prompts — one focused question per AFM call
AFM_PROMPTS = {
    "summary": "Summarize this conversation in 2-3 sentences. Another AI will use this to re-establish context.\n\nConversation:\n{chunk_text}",
    "decided": "Was anything decided or agreed upon in this conversation? Be concise.\n\nConversation:\n{chunk_text}",
    "unresolved": "What is still unresolved or left open at the end of this conversation? Be concise.\n\nConversation:\n{chunk_text}",
}

FILE_TOOLS = {"Read", "Edit", "Write", "Glob", "Grep"}


def extract_mechanical_metadata(chunk) -> dict:
    """Extract file paths, commands, and human messages mechanically from the chunk."""
    meta = {}

    # Unique file paths from file-related tool calls
    files = []
    seen_files = set()
    for tc in chunk.tool_calls:
        if tc["tool"] in FILE_TOOLS and tc["target"]:
            path = tc["target"]
            if path not in seen_files:
                seen_files.add(path)
                files.append(path)
    if files:
        meta["files"] = files

    # Bash commands (trimmed)
    commands = []
    for tc in chunk.tool_calls:
        if tc["tool"] == "Bash" and tc["target"]:
            commands.append(tc["target"][:100])
    if commands:
        meta["commands"] = commands[:10]

    # All human messages, compressed
    human_msgs = []
    for msg in chunk.messages:
        if msg.get("role") == "user" and msg.get("is_human"):
            text = msg["text"][:80].replace("\n", " ").strip()
            if text:
                human_msgs.append(text)
    if human_msgs:
        meta["human_messages"] = human_msgs

    return meta


class MemorableDaemon:
    """Main daemon that watches transcripts and generates AFM anchors."""

    def __init__(self, enable_anchors: bool = True):
        self.enable_anchors = enable_anchors
        ANCHORS_DIR.mkdir(parents=True, exist_ok=True)

    def on_chunk(self, session_id: str, chunk):
        """Called for each conversation chunk (~3 human messages)."""
        if not self.enable_anchors:
            return

        try:
            self._generate_anchor(session_id, chunk)
        except Exception:
            logger.exception("Anchor generation failed for %s", session_id)

    def _call_afm(self, prompt: str) -> str | None:
        """Make a single AFM call. Returns response text or None on failure."""
        try:
            result = subprocess.run(
                [AFM_BIN, "-s", prompt, "-i", AFM_SYSTEM],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning("AFM returned %d: %s", result.returncode, result.stderr[:200])
                return None
            response = result.stdout.strip()
            return response if response else None
        except subprocess.TimeoutExpired:
            logger.warning("AFM timed out")
            return None
        except FileNotFoundError:
            logger.error("AFM binary not found at %s", AFM_BIN)
            return None

    def _generate_anchor(self, session_id: str, chunk):
        """Multi-pass AFM extraction + mechanical metadata."""
        conversation = chunk.text(max_assistant_len=300)
        if len(conversation) < 20:
            return

        # Cap at ~2000 chars for AFM's 4K context limit
        chunk_text = conversation[:2000]

        logger.info("Anchor: session=%s chunk=#%d (%d msgs)",
                     session_id, chunk.chunk_number, len(chunk.messages))

        # Multi-pass AFM — one focused question per call
        afm_fields = {}
        for field_name, prompt_template in AFM_PROMPTS.items():
            prompt = prompt_template.format(chunk_text=chunk_text)
            response = self._call_afm(prompt)
            if response:
                afm_fields[field_name] = response
                logger.debug("AFM %s: %s", field_name, response[:200])

        if not afm_fields.get("summary"):
            logger.warning("AFM summary failed, skipping anchor")
            return

        # Mechanical metadata — free, no AFM needed
        mechanical = extract_mechanical_metadata(chunk)

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session_id,
            "chunk": chunk.chunk_number,
            **afm_fields,
            **mechanical,
        }

        # Append to per-machine JSONL
        anchor_file = ANCHORS_DIR / f"{MACHINE_ID}.jsonl"
        with open(anchor_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.info("Anchor written: %s", afm_fields["summary"][:80])


def main():
    parser = argparse.ArgumentParser(description="Memorable background daemon")
    parser.add_argument("--no-anchors", action="store_true", help="Disable AFM anchor generation")
    parser.add_argument("--idle-timeout", type=float, default=300.0, help="Seconds before flushing idle session")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    daemon = MemorableDaemon(enable_anchors=not args.no_anchors)

    logger.info("Memorable daemon starting")
    logger.info("  AFM: %s", AFM_BIN)
    logger.info("  Anchors: %s", "enabled" if not args.no_anchors else "disabled")
    logger.info("  Machine: %s", MACHINE_ID)

    watch_transcripts(
        on_chunk=daemon.on_chunk if not args.no_anchors else None,
        on_human_message=None,
        chunk_every=3,
        idle_timeout=args.idle_timeout,
    )


if __name__ == "__main__":
    main()
