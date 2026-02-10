#!/usr/bin/env python3
"""Memorable background daemon.

Watches Claude Code session transcripts in real-time and:
1. Saves a clean conversation log (human/assistant text, no cruft)
2. Generates session notes when sessions go idle

The conversation log at ~/.memorable/data/transcripts/{hostname}.md
serves as a rolling record of all sessions. On compaction or session
start, Claude can read the tail to re-establish context.

Usage:
    python3 memorable_daemon.py
    python3 memorable_daemon.py --no-transcript   # watch only, no logging
"""

import argparse
import logging
import socket
from datetime import datetime, timezone
from pathlib import Path

from transcript_watcher import watch_transcripts
from note_generator import generate_note

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"

MACHINE_ID = socket.gethostname()


class MemorableDaemon:
    """Main daemon that watches transcripts and saves clean conversation logs."""

    def __init__(self, enable_transcript: bool = True, enable_notes: bool = True):
        self.enable_transcript = enable_transcript
        self.enable_notes = enable_notes
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        self._current_session: str | None = None

    def on_chunk(self, session_id: str, chunk):
        """Called for each conversation chunk. Writes clean conversation to transcript."""
        if not self.enable_transcript:
            return

        try:
            self._write_transcript(session_id, chunk)
        except Exception:
            logger.exception("Transcript write failed for %s", session_id)

    def _write_transcript(self, session_id: str, chunk):
        """Write clean human/assistant messages to the rolling transcript file."""
        if not chunk.messages:
            return

        transcript_file = TRANSCRIPTS_DIR / f"{MACHINE_ID}.md"

        lines = []

        # Add session header if this is a new session
        if session_id != self._current_session:
            self._current_session = session_id
            now = datetime.now(timezone.utc)
            ts_str = now.strftime("%Y-%m-%d %H:%M")
            lines.append(f"\n---\n**Session** [{ts_str}]\n")

        for msg in chunk.messages:
            role = "Human" if msg["role"] == "user" else "Claude"
            text = msg["text"].strip()
            # Truncate very long messages
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"**{role}:** {text}\n")

        with open(transcript_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info("Transcript: session=%s chunk=#%d (%d msgs written)",
                     session_id, chunk.chunk_number, len(chunk.messages))

    def on_session_idle(self, session_id: str, transcript_path: str, human_count: int):
        """Called when a session goes idle. Generates a session note via LLM."""
        if not self.enable_notes:
            return

        # Skip subagent transcripts (session_id contains '/')
        if "/" in session_id:
            logger.debug("Skipping subagent session: %s", session_id)
            return

        if human_count < 3:
            logger.info("Session %s too short (%d msgs), skipping note", session_id, human_count)
            return

        logger.info("Generating note for idle session %s (%d human msgs)", session_id, human_count)

        try:
            success = generate_note(session_id, transcript_path, machine_id=MACHINE_ID)
            if success:
                logger.info("Note generated for session %s", session_id)
            else:
                logger.info("Note generation skipped for session %s", session_id)
        except Exception:
            logger.exception("Note generation failed for session %s", session_id)


def main():
    parser = argparse.ArgumentParser(description="Memorable background daemon")
    parser.add_argument("--no-transcript", action="store_true", help="Disable conversation transcript logging")
    parser.add_argument("--no-notes", action="store_true", help="Disable session note generation")
    parser.add_argument("--idle-timeout", type=float, default=300.0, help="Seconds before flushing idle session")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    daemon = MemorableDaemon(
        enable_transcript=not args.no_transcript,
        enable_notes=not args.no_notes,
    )

    logger.info("Memorable daemon starting")
    logger.info("  Transcript: %s", "enabled" if not args.no_transcript else "disabled")
    logger.info("  Notes: %s", "enabled" if not args.no_notes else "disabled")
    logger.info("  Machine: %s", MACHINE_ID)

    watch_transcripts(
        on_chunk=daemon.on_chunk if not args.no_transcript else None,
        on_human_message=None,
        on_session_idle=daemon.on_session_idle,
        chunk_every=10,
        idle_timeout=args.idle_timeout,
    )


if __name__ == "__main__":
    main()
