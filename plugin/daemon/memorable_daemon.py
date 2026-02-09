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

AFM_SYSTEM = "You extract structured information from conversations. Output ONLY the filled template, nothing else."

AFM_PROMPT = """Read this conversation excerpt. Fill in each field precisely.

TOPIC: [Main subject, max 10 words]
DOING: [Current task/action, max 10 words]
DECIDED: [Any choice made, or 'none']
MOOD: [One word]
UNRESOLVED: [Open question or unfinished item, or 'none']
KEYWORDS: [5-8 important nouns/names from the messages, comma separated]
QUOTE: [One significant sentence from the human, quoted exactly]

Conversation:
{chunk_text}"""


def parse_afm_output(text: str) -> dict:
    """Parse AFM structured output into a dict."""
    fields = {}
    for label in ("TOPIC", "DOING", "DECIDED", "MOOD", "UNRESOLVED", "KEYWORDS", "QUOTE"):
        match = re.search(rf'{label}:\s*(.+)', text)
        if match:
            value = match.group(1).strip()
            if label == "KEYWORDS":
                fields[label.lower()] = [k.strip() for k in value.split(",") if k.strip()]
            else:
                fields[label.lower()] = value
    return fields


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

    def _generate_anchor(self, session_id: str, chunk):
        """Call AFM to extract a structured anchor from the chunk."""
        conversation = chunk.text(max_assistant_len=300)
        if len(conversation) < 20:
            return

        # Cap at ~2000 chars for AFM's 4K context limit
        prompt = AFM_PROMPT.format(chunk_text=conversation[:2000])

        logger.info("Anchor: session=%s chunk=#%d (%d msgs)",
                     session_id, chunk.chunk_number, len(chunk.messages))

        try:
            result = subprocess.run(
                [AFM_BIN, "-s", prompt, "-i", AFM_SYSTEM],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning("AFM returned %d: %s", result.returncode, result.stderr[:200])
                return
            response = result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.warning("AFM timed out for session %s chunk #%d", session_id, chunk.chunk_number)
            return
        except FileNotFoundError:
            logger.error("AFM binary not found at %s", AFM_BIN)
            return

        if not response:
            return

        logger.debug("AFM response: %s", response[:300])

        fields = parse_afm_output(response)
        if not fields.get("topic"):
            logger.warning("AFM output missing TOPIC, skipping")
            return

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session_id,
            "chunk": chunk.chunk_number,
            **fields,
        }

        # Append to per-machine JSONL
        anchor_file = ANCHORS_DIR / f"{MACHINE_ID}.jsonl"
        with open(anchor_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.info("Anchor written: %s — %s", fields.get("topic", "?"), fields.get("doing", "?"))


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
