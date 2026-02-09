#!/usr/bin/env python3
"""Memorable background daemon.

Watches Claude Code session transcripts in real-time and:
1. Every human message: checks for relevance against knowledge graph
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
from knowledge_graph import build_knowledge_graph, load_topic_index

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"
HINTS_DIR = DATA_DIR / "hints"

MACHINE_ID = socket.gethostname()

# --- Prompts ---

RELEVANCE_SYSTEM = "You check if a message connects to any known topics. Be concise."

RELEVANCE_PROMPT = """A human just sent this message in a conversation with an AI assistant:

"{message}"

Here are topics from their recent history. Each has an address (file:line) for retrieval:

{topics}

Does this message clearly relate to any of these topics? Consider:
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

        # Knowledge graph / topic index (loaded once, refreshed periodically)
        self._topic_index: str = ""
        self._topic_index_loaded_at: float = 0

        self._load_topic_index()

        # Ensure directories exist
        HINTS_DIR.mkdir(parents=True, exist_ok=True)

    def on_human_message(self, session_id: str, message: str):
        """Called for every human message (for relevance checking)."""
        if not self.enable_relevance or not self._topic_index:
            return

        # Refresh topic index every 10 minutes
        if time.monotonic() - self._topic_index_loaded_at > 600:
            self._load_topic_index()

        try:
            self._check_relevance(session_id, message)
        except Exception:
            logger.exception("Relevance check failed for %s", session_id)

    def _check_relevance(self, session_id: str, message: str):
        """Check if a human message matches known topics.

        Sends every message directly to the 3B model with the full topic index.
        The model decides whether there's a genuine connection.
        """
        if len(message) < 10:
            return

        prompt = RELEVANCE_PROMPT.format(
            message=message[:500],
            topics=self._topic_index,
        )

        logger.info("Relevance check: session=%s msg=%s", session_id, message[:80])

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

    def _load_topic_index(self):
        """Load topic index from knowledge graph for relevance matching."""
        try:
            build_knowledge_graph()
            self._topic_index = load_topic_index(max_topics=30)
            self._topic_index_loaded_at = time.monotonic()
            logger.info("Topic index loaded (%d chars)", len(self._topic_index))
        except Exception:
            logger.exception("Failed to build/load knowledge graph")
            self._topic_index = ""


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
        on_chunk=None,
        on_human_message=daemon.on_human_message if not args.no_relevance else None,
        chunk_every=15,
        idle_timeout=args.idle_timeout,
    )


# Default URL (can be overridden via --primary-url or config)
DEFAULT_PRIMARY_URL = "http://192.168.68.58:8400/v1/chat/completions"


if __name__ == "__main__":
    main()
