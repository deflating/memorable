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
import logging
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from transcript_watcher import watch_transcripts
from note_generator import generate_note

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"
ANCHORS_DIR = DATA_DIR / "anchors"

MACHINE_ID = socket.gethostname()

AFM_BIN = shutil.which("afm") or "/opt/homebrew/bin/afm"

AFM_SYSTEM = "You are a conversation summarizer. Be concise."

AFM_PROMPT = "Summarize this conversation in 2-3 sentences for another AI to re-establish context. Include what was being worked on, any decisions made, and anything left unresolved.\n\nConversation:\n{chunk_text}"


class MemorableDaemon:
    """Main daemon that watches transcripts and generates AFM anchors and session notes."""

    def __init__(self, enable_anchors: bool = True, enable_notes: bool = True):
        self.enable_anchors = enable_anchors
        self.enable_notes = enable_notes
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
        """Single AFM summary + mechanical metadata → plain text anchor."""
        conversation = chunk.text(max_assistant_len=300)
        if len(conversation) < 20:
            return

        # Cap at ~2000 chars for AFM's 4K context limit
        chunk_text = conversation[:2000]

        logger.info("Anchor: session=%s chunk=#%d (%d msgs)",
                     session_id, chunk.chunk_number, len(chunk.messages))

        prompt = AFM_PROMPT.format(chunk_text=chunk_text)
        summary = self._call_afm(prompt)
        if not summary:
            logger.warning("AFM summary failed, skipping anchor")
            return

        # Clean up — collapse to single line
        summary = " ".join(summary.split())

        now = datetime.now(timezone.utc)
        ts_str = now.strftime("%Y-%m-%d %H:%M")

        # Build plain text anchor line
        anchor_line = f"[{ts_str}] {summary}"

        anchor_file = ANCHORS_DIR / f"{MACHINE_ID}.md"

        # Rotate: drop lines older than 24h
        self._rotate_anchors(anchor_file, now)

        # Append
        with open(anchor_file, "a") as f:
            f.write(anchor_line + "\n")

        logger.info("Anchor written: %s", summary[:80])

    def _rotate_anchors(self, anchor_file: Path, now: datetime):
        """Remove anchor lines older than 24 hours."""
        if not anchor_file.exists():
            return

        try:
            lines = anchor_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return

        kept = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Parse timestamp from [YYYY-MM-DD HH:MM] prefix
            if line.startswith("[") and "]" in line:
                ts_part = line[1:line.index("]")]
                try:
                    anchor_dt = datetime.strptime(ts_part, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    age_hours = (now - anchor_dt).total_seconds() / 3600
                    if age_hours <= 24:
                        kept.append(line)
                    continue
                except ValueError:
                    pass
            # Keep lines we can't parse (shouldn't happen, but safe)
            kept.append(line)

        anchor_file.write_text("\n".join(kept) + "\n" if kept else "", encoding="utf-8")

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
    parser.add_argument("--no-anchors", action="store_true", help="Disable AFM anchor generation")
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
        enable_anchors=not args.no_anchors,
        enable_notes=not args.no_notes,
    )

    logger.info("Memorable daemon starting")
    logger.info("  AFM: %s", AFM_BIN)
    logger.info("  Anchors: %s", "enabled" if not args.no_anchors else "disabled")
    logger.info("  Notes: %s", "enabled" if not args.no_notes else "disabled")
    logger.info("  Machine: %s", MACHINE_ID)

    watch_transcripts(
        on_chunk=daemon.on_chunk if not args.no_anchors else None,
        on_human_message=None,
        on_session_idle=daemon.on_session_idle,
        chunk_every=3,
        idle_timeout=args.idle_timeout,
    )


if __name__ == "__main__":
    main()
