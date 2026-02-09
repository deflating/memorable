"""Real-time transcript watcher for Claude Code sessions.

Watches ~/.claude/projects/*/*.jsonl for new lines, parses them
incrementally, and emits conversation chunks every N human messages
for downstream summarization.

Usage:
    from transcript_watcher import watch_transcripts

    def on_chunk(session_id, chunk):
        # chunk is a TranscriptChunk with .messages, .tool_calls, .text()
        print(chunk.text())

    watch_transcripts(on_chunk=on_chunk, chunk_every=15)
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path.home() / ".claude" / "projects"

CONTENT_TYPES = {"user", "assistant"}


@dataclass
class TranscriptChunk:
    """A chunk of conversation ready for summarization."""
    session_id: str
    chunk_number: int
    messages: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    human_count: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None

    def text(self, max_assistant_len: int = 500) -> str:
        """Format chunk as interleaved transcript text."""
        parts = []
        for msg in self.messages:
            role = "Human" if msg["role"] == "user" else "Claude"
            text = msg["text"]
            if msg["role"] == "assistant" and len(text) > max_assistant_len:
                text = text[:max_assistant_len] + "..."
            parts.append(f"**{role}:** {text}")

        if self.tool_calls:
            parts.append("\n## Tool Calls")
            for tc in self.tool_calls[-30:]:
                parts.append(f"- {tc['tool']}: {tc['target']}")

        return "\n\n".join(parts)


class TranscriptChunker:
    """Tracks parsing state for a single JSONL session file.

    Reads incrementally from the last-known file position, parses new
    lines, and emits a TranscriptChunk every chunk_every human messages.
    """

    def __init__(self, path: Path, session_id: str, chunk_every: int = 15, skip_existing: bool = False):
        self.path = path
        self.session_id = session_id
        self.chunk_every = chunk_every

        # Start at end of file if skipping existing content
        if skip_existing and path.exists():
            self._offset = path.stat().st_size
        else:
            self._offset = 0

        self._messages: list = []
        self._tool_calls: list = []
        self._human_count = 0
        self._chunk_number = 0
        self._first_ts: Optional[str] = None
        self._last_ts: Optional[str] = None

        self.total_human_count = 0
        self.total_lines = 0

    def read_new_lines(self) -> list[dict]:
        """Read any new lines appended since our last read."""
        entries = []
        try:
            # Detect file truncation (e.g. context compaction rewrites the JSONL)
            current_size = self.path.stat().st_size
            if current_size < self._offset:
                logger.info("File truncated (compaction?): %s (was %d, now %d)",
                            self.path.name, self._offset, current_size)
                self._offset = 0

            with open(self.path, "r") as f:
                f.seek(self._offset)
                for line in f:
                    self.total_lines += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    entries.append(entry)
                self._offset = f.tell()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Error reading %s: %s", self.path, e)
        return entries

    def process(self) -> tuple[list[TranscriptChunk], list[str]]:
        """Read new lines and return completed chunks and new human messages.

        Call this whenever the file is modified. Returns:
        - chunks: list of TranscriptChunk objects
        - human_messages: list of new human message texts (for per-message processing)
        """
        entries = self.read_new_lines()
        chunks = []
        human_messages = []

        for entry in entries:
            msg_type = entry.get("type")

            if entry.get("isSidechain"):
                continue

            ts = entry.get("timestamp")
            if ts:
                if self._first_ts is None:
                    self._first_ts = ts
                self._last_ts = ts

            if msg_type not in CONTENT_TYPES:
                continue

            if msg_type == "user":
                parsed = self._parse_user(entry)
                if parsed:
                    self._messages.append(parsed)
                    if parsed.get("is_human"):
                        self._human_count += 1
                        self.total_human_count += 1
                        human_messages.append(parsed["text"])

            elif msg_type == "assistant":
                text_parts, tools = self._parse_assistant(entry)
                for text in text_parts:
                    self._messages.append({"role": "assistant", "text": text})
                self._tool_calls.extend(tools)

            if self._human_count >= self.chunk_every:
                chunk = self._emit_chunk()
                chunks.append(chunk)

        return chunks, human_messages

    def flush(self) -> Optional[TranscriptChunk]:
        """Flush remaining messages as a final chunk.

        Call this when a session appears to have ended (file went quiet).
        Returns None if there is nothing meaningful to flush.
        """
        if self._human_count < 1:
            return None
        return self._emit_chunk()

    def _emit_chunk(self) -> TranscriptChunk:
        """Package current accumulator into a chunk and reset."""
        self._chunk_number += 1
        chunk = TranscriptChunk(
            session_id=self.session_id,
            chunk_number=self._chunk_number,
            messages=self._messages,
            tool_calls=self._tool_calls,
            human_count=self._human_count,
            first_ts=self._first_ts,
            last_ts=self._last_ts,
        )
        self._messages = []
        self._tool_calls = []
        self._human_count = 0
        self._first_ts = None
        self._last_ts = None
        return chunk

    @staticmethod
    def _parse_user(entry: dict) -> Optional[dict]:
        """Parse a user-type JSONL entry.

        String content = actual human message.
        List content = tool results (skipped entirely).
        """
        message = entry.get("message", {})
        content = message.get("content")

        if isinstance(content, str):
            clean = re.sub(
                r'<system-reminder>.*?</system-reminder>',
                '', content, flags=re.DOTALL
            ).strip()
            if clean and len(clean) > 3:
                return {"role": "user", "text": clean[:2000], "is_human": True}

        return None

    @staticmethod
    def _parse_assistant(entry: dict) -> tuple[list[str], list[dict]]:
        """Parse an assistant-type JSONL entry.

        Returns (text_parts, tool_calls).
        """
        message = entry.get("message", {})
        content = message.get("content")
        texts = []
        tools = []

        if not isinstance(content, list):
            return texts, tools

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")

            if block_type == "text":
                text = block.get("text", "")
                if text and len(text) > 10:
                    texts.append(text[:3000])

            elif block_type == "tool_use":
                tool_name = block.get("name", "")
                tool_input = block.get("input", {})
                target = (
                    tool_input.get("file_path", "")
                    or tool_input.get("path", "")
                    or tool_input.get("pattern", "")
                    or tool_input.get("command", "")
                )
                tools.append({
                    "tool": tool_name,
                    "target": str(target)[:200],
                })

        return texts, tools


class _TranscriptHandler(FileSystemEventHandler):
    """Watchdog handler that routes file events to the right chunker."""

    def __init__(
        self,
        on_chunk: Callable[[str, TranscriptChunk], None],
        on_human_message: Optional[Callable[[str, str], None]] = None,
        on_session_idle: Optional[Callable[[str, str, int], None]] = None,
        chunk_every: int = 15,
        idle_timeout: float = 300.0,
        skip_existing: bool = True,
    ):
        super().__init__()
        self.on_chunk = on_chunk
        self.on_human_message = on_human_message
        self.on_session_idle = on_session_idle
        self.chunk_every = chunk_every
        self.idle_timeout = idle_timeout
        self.skip_existing = skip_existing
        self._chunkers: dict[str, TranscriptChunker] = {}
        self._last_activity: dict[str, float] = {}

    def _session_id_from_path(self, path: str) -> Optional[str]:
        """Extract session ID from a JSONL file path.

        Main sessions:  .../UUID.jsonl  ->  UUID
        Subagents:      .../UUID/subagents/agent-xxx.jsonl  ->  UUID/agent-xxx
        """
        p = Path(path)
        if p.suffix != ".jsonl":
            return None

        if p.parent.name == "subagents":
            parent_session = p.parent.parent.name
            return f"{parent_session}/{p.stem}"

        return p.stem

    def _get_chunker(self, path: str) -> Optional[TranscriptChunker]:
        """Get or create a chunker for the given file path."""
        session_id = self._session_id_from_path(path)
        if session_id is None:
            return None

        if path not in self._chunkers:
            self._chunkers[path] = TranscriptChunker(
                path=Path(path),
                session_id=session_id,
                chunk_every=self.chunk_every,
                skip_existing=self.skip_existing,
            )
            logger.info("Tracking new session: %s (%s)", session_id, path)

        return self._chunkers[path]

    def on_created(self, event):
        if isinstance(event, FileCreatedEvent) and event.src_path.endswith(".jsonl"):
            self._handle_change(event.src_path)

    def on_modified(self, event):
        if isinstance(event, FileModifiedEvent) and event.src_path.endswith(".jsonl"):
            self._handle_change(event.src_path)

    def _handle_change(self, path: str):
        """Process new content in a JSONL file."""
        chunker = self._get_chunker(path)
        if chunker is None:
            return

        self._last_activity[path] = time.monotonic()
        session_id = self._session_id_from_path(path)

        chunks, human_messages = chunker.process()

        # Fire per-message callback for relevance checking
        if self.on_human_message and session_id:
            for msg in human_messages:
                try:
                    self.on_human_message(session_id, msg)
                except Exception:
                    logger.exception("Error in on_human_message for %s", session_id)

        for chunk in chunks:
            if self.on_chunk:
                try:
                    self.on_chunk(chunk.session_id, chunk)
                except Exception:
                    logger.exception("Error in on_chunk callback for %s", chunk.session_id)

    def check_idle_sessions(self):
        """Flush and clean up sessions that have gone quiet."""
        now = time.monotonic()
        stale = []

        for path, last in self._last_activity.items():
            if now - last > self.idle_timeout:
                stale.append(path)

        for path in stale:
            chunker = self._chunkers.get(path)
            if chunker:
                chunk = chunker.flush()
                if chunk and self.on_chunk:
                    try:
                        self.on_chunk(chunk.session_id, chunk)
                    except Exception:
                        logger.exception("Error flushing chunk for %s", chunker.session_id)

                session_id = chunker.session_id
                total = chunker.total_human_count
                logger.info(
                    "Session idle, flushed: %s (%d total human msgs)",
                    session_id, total,
                )

                # Notify daemon that this session is idle (for note generation)
                if self.on_session_idle:
                    try:
                        self.on_session_idle(session_id, path, total)
                    except Exception:
                        logger.exception("Error in on_session_idle for %s", session_id)

                del self._chunkers[path]
            del self._last_activity[path]

    @property
    def active_sessions(self) -> int:
        return len(self._chunkers)


def watch_transcripts(
    on_chunk: Callable[[str, TranscriptChunk], None],
    on_human_message: Optional[Callable[[str, str], None]] = None,
    on_session_idle: Optional[Callable[[str, str, int], None]] = None,
    chunk_every: int = 15,
    idle_timeout: float = 300.0,
    idle_check_interval: float = 60.0,
    projects_dir: Optional[Path] = None,
):
    """Watch Claude Code transcript files and emit conversation chunks.

    Args:
        on_chunk: Called with (session_id, TranscriptChunk) when a chunk
                  of chunk_every human messages is ready.
        on_human_message: Called with (session_id, message_text) for every
                          new human message. Use for per-message processing
                          like relevance checking.
        on_session_idle: Called with (session_id, transcript_path, human_count)
                         when a session goes idle. Use for end-of-session
                         processing like note generation.
        chunk_every: Number of human messages per chunk (default 15).
        idle_timeout: Seconds of inactivity before flushing remaining
                      messages as a final chunk (default 300).
        idle_check_interval: How often to check for idle sessions (default 60).
        projects_dir: Override the default ~/.claude/projects directory.

    Blocks forever until KeyboardInterrupt.
    """
    watch_dir = projects_dir or PROJECTS_DIR
    if not watch_dir.exists():
        raise FileNotFoundError(f"Projects directory not found: {watch_dir}")

    handler = _TranscriptHandler(
        on_chunk=on_chunk,
        on_human_message=on_human_message,
        on_session_idle=on_session_idle,
        chunk_every=chunk_every,
        idle_timeout=idle_timeout,
    )

    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()

    logger.info(
        "Watching %s for transcript changes (chunk_every=%d, idle_timeout=%.0fs)",
        watch_dir, chunk_every, idle_timeout,
    )

    try:
        while True:
            time.sleep(idle_check_interval)
            handler.check_idle_sessions()
            if handler.active_sessions > 0:
                logger.debug("Active sessions: %d", handler.active_sessions)
    except KeyboardInterrupt:
        logger.info("Shutting down transcript watcher")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    def _print_chunk(session_id: str, chunk: TranscriptChunk):
        print(f"\n{'='*60}")
        print(f"Session: {session_id}  |  Chunk #{chunk.chunk_number}  |  {chunk.human_count} human msgs")
        print(f"Time range: {chunk.first_ts} -> {chunk.last_ts}")
        print(f"Messages: {len(chunk.messages)}  |  Tool calls: {len(chunk.tool_calls)}")
        print(f"{'='*60}")
        preview = chunk.text()
        if len(preview) > 1000:
            preview = preview[:1000] + "\n..."
        print(preview)
        print()

    print(f"Watching: {PROJECTS_DIR}")
    print("Chunk every 15 human messages, idle timeout 300s")
    print("Press Ctrl+C to stop.\n")

    watch_transcripts(on_chunk=_print_chunk)
