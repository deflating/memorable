#!/usr/bin/env python3
"""Memorable background daemon.

Watches Claude Code session transcripts in real-time and:
1. Every few messages: checks conversation chunk for relevance against knowledge graph
2. On relevance hit: writes a context hint for hooks to inject

Usage:
    python3 memorable_daemon.py
    python3 memorable_daemon.py --primary-url http://192.168.1.50:8400/v1/chat/completions
"""

import argparse
import json
import logging
import socket
import time
from pathlib import Path

from transcript_watcher import watch_transcripts
from inference import InferenceClient

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"
HINTS_DIR = DATA_DIR / "hints"

MACHINE_ID = socket.gethostname()

# --- Prompts ---

RELEVANCE_SYSTEM = "You check if a conversation connects to any past sessions. Be concise."

RELEVANCE_PROMPT = """Here is a recent exchange between a human and an AI assistant:

{conversation}

Here are summaries of their past sessions. Each has an address (file:line) for retrieval:

{sessions}

Does this conversation clearly relate to any of these past sessions? Consider:
- Same subject being revisited
- A decision being reconsidered
- A person mentioned before
- A problem previously discussed

If YES, list the matching addresses (up to 3, most relevant first):
MATCH
- [address] — [3-5 word reason]
- [address] — [reason]

If NO clear match:
SKIP

Be conservative — only match genuine connections, not surface-level word overlap."""


class MemorableDaemon:
    """Main daemon that coordinates watching, inference, and storage."""

    def __init__(
        self,
        client: InferenceClient,
        enable_relevance: bool = True,
    ):
        self.client = client
        self.enable_relevance = enable_relevance

        # Session index (loaded once, refreshed periodically)
        self._session_index: str = ""
        self._session_index_loaded_at: float = 0

        self._load_session_index()

        # Ensure directories exist
        HINTS_DIR.mkdir(parents=True, exist_ok=True)

    def on_chunk(self, session_id: str, chunk):
        """Called for each conversation chunk (human + assistant messages)."""
        if not self.enable_relevance or not self._session_index:
            return

        # Refresh session index every 10 minutes
        if time.monotonic() - self._session_index_loaded_at > 600:
            self._load_session_index()

        try:
            self._check_relevance(session_id, chunk)
        except Exception:
            logger.exception("Relevance check failed for %s", session_id)

    def _check_relevance(self, session_id: str, chunk):
        """Check if a conversation chunk matches known topics.

        Uses chunk.text() which includes both human and assistant messages,
        giving much better signal than human messages alone.
        """
        conversation = chunk.text(max_assistant_len=300)
        if len(conversation) < 20:
            return

        prompt = RELEVANCE_PROMPT.format(
            conversation=conversation[:2000],
            sessions=self._session_index,
        )

        logger.info("Relevance check: session=%s chunk=#%d (%d msgs)", session_id, chunk.chunk_number, len(chunk.messages))

        response = self.client.chat(
            prompt=prompt,
            system=RELEVANCE_SYSTEM,
            max_tokens=150,
            temperature=0.2,
        )

        response = response.strip()
        logger.debug("3B response: %s", response[:200])

        if response.upper().startswith("SKIP"):
            return

        if response.upper().startswith("MATCH"):
            addresses = []
            for line in response.split("\n"):
                line = line.strip()
                if line.startswith("- ") and "—" in line:
                    addresses.append(line[2:])

            if addresses:
                logger.info("Relevance match for session %s: %d addresses", session_id, len(addresses))
                self._write_hint(session_id, addresses)

    def _write_hint(self, session_id: str, addresses: list[str]):
        """Write a context hint file for hooks to pick up.

        The hint is a simple text file with suggested addresses for Claude
        to read. Hooks inject this as a system reminder. Claude decides
        whether to actually read them.
        """
        lines = ["[Memorable] Past context that may be relevant:", ""]
        for addr in addresses[:3]:
            lines.append(f"- {addr}")
        lines.append("")
        lines.append("Read these if they seem relevant to the current conversation.")

        hint_file = HINTS_DIR / f"{session_id.replace('/', '_')}.txt"
        hint_file.write_text("\n".join(lines))

        logger.info("Hint written: %s (%d addresses)", hint_file.name, len(addresses))

    def _load_session_index(self, max_chars: int = 6000):
        """Build a compact index of session note summaries for relevance matching.

        Reads all session notes, extracts the first sentence of each summary,
        deduplicates, and formats as an addressable list.
        """
        notes_dir = DATA_DIR / "notes"
        if not notes_dir.exists():
            self._session_index = ""
            return

        try:
            all_notes = []
            for f in sorted(notes_dir.glob("*.jsonl")):
                for i, line in enumerate(f.read_text().splitlines()):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        n = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    n["_file"] = f.name
                    n["_line"] = i
                    all_notes.append(n)

            # Newest first (by line number descending, since batch wrote in order)
            all_notes.reverse()

            # Build index: one line per note with address + summary
            seen = set()
            lines = []
            total_chars = 0
            for n in all_notes:
                note = n.get("note", "")
                # Extract first non-header line as summary
                summary = ""
                for text_line in note.split("\n"):
                    text_line = text_line.strip()
                    if text_line and not text_line.startswith("#") and not text_line.startswith("---"):
                        summary = text_line[:150]
                        break
                if not summary:
                    continue
                # Deduplicate near-identical summaries
                dedup_key = summary[:80]
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                addr = f"{n['_file']}:{n['_line']}"
                entry = f"{addr} {summary}"
                if total_chars + len(entry) > max_chars:
                    break
                lines.append(entry)
                total_chars += len(entry) + 1

            self._session_index = "\n".join(lines)
            self._session_index_loaded_at = time.monotonic()
            logger.info("Session index loaded (%d notes, %d chars)", len(lines), len(self._session_index))
        except Exception:
            logger.exception("Failed to load session index")
            self._session_index = ""


def get_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


def main():
    parser = argparse.ArgumentParser(description="Memorable background daemon")
    parser.add_argument("--primary-url", type=str, default=DEFAULT_PRIMARY_URL, help="MLX server URL")
    parser.add_argument("--no-relevance", action="store_true", help="Disable per-message relevance checking")
    parser.add_argument("--idle-timeout", type=float, default=300.0, help="Seconds before flushing idle session")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cfg = get_config()
    fallback_key = cfg.get("summarizer", {}).get("api_key", "")

    client = InferenceClient(
        primary_url=args.primary_url,
        fallback_key=fallback_key,
    )

    daemon = MemorableDaemon(
        client=client,
        enable_relevance=not args.no_relevance,
    )

    logger.info("Memorable daemon starting")
    logger.info("  Primary: %s", args.primary_url)
    logger.info("  Fallback: %s", "DeepSeek" if fallback_key else "NONE")
    logger.info("  Relevance: %s", "enabled" if not args.no_relevance else "disabled")
    logger.info("  Machine: %s", MACHINE_ID)

    # Check primary on startup
    if client.check_primary():
        logger.info("Primary endpoint is reachable")
    else:
        logger.warning("Primary endpoint not reachable — will use fallback")

    watch_transcripts(
        on_chunk=daemon.on_chunk if not args.no_relevance else None,
        on_human_message=None,
        chunk_every=3,
        idle_timeout=args.idle_timeout,
    )


# Default URL (can be overridden via --primary-url or config)
DEFAULT_PRIMARY_URL = "http://192.168.68.58:8400/v1/chat/completions"


if __name__ == "__main__":
    main()
