"""Transcript processing pipeline for Memorable.

Reads .jsonl transcripts, filters with heuristics, compresses with
LLMLingua-2 at 0.50 for searchable archive, then uses Apple's
on-device Foundation Model to generate session notes and emoji headers.

Fully local. No cloud APIs. No API keys.
"""

import json
import hashlib
import re
import subprocess
import time
from pathlib import Path
from datetime import datetime

from .db import MemorableDB
from .config import Config


# Lazy-loaded compressor (LLMLingua model is ~500MB, only load once)
_compressor = None


def _get_compressor():
    global _compressor
    if _compressor is None:
        from llmlingua import PromptCompressor
        _compressor = PromptCompressor(
            model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
            use_llmlingua2=True,
            device_map="cpu",
        )
    return _compressor


def _call_apple_model(prompt: str, instructions: str = "") -> str:
    """Call Apple's on-device Foundation Model via afm CLI."""
    env = {**__import__("os").environ, "TOKENIZERS_PARALLELISM": "false"}
    cmd = ["afm", "-s", prompt]
    if instructions:
        cmd.extend(["-i", instructions])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, env=env
        )
        output = result.stdout.strip()
        # Check for context window errors
        if "Context window exceeded" in output or "Context window exceeded" in (result.stderr or ""):
            return "[Apple model: context too long, skipped]"
        return output
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"[Apple model error: {e}]"


class TranscriptProcessor:
    def __init__(self, config: Config):
        self.config = config
        self.db = MemorableDB(Path(config["db_path"]))
        self.min_messages = config.get("min_messages", 15)
        self.min_human_words = config.get("min_human_words", 100)
        self.stale_minutes = config.get("stale_minutes", 15)
        self.rate_storage = config.get("compression_rate_storage", 0.50)

    def process_all(self):
        """Scan transcript directories, queue new files, process pending."""
        self._scan_for_new_transcripts()
        pending = self.db.get_pending_transcripts()
        print(f"Processing {len(pending)} pending transcript(s)...")

        for item in pending:
            try:
                self._process_one(item)
            except Exception as e:
                print(f"  Error processing {item['transcript_path']}: {e}")
                self.db.mark_processed(item["id"], error=str(e))

    def _scan_for_new_transcripts(self):
        """Find new/changed transcripts and add to queue."""
        for transcript_dir in self.config["transcript_dirs"]:
            base = Path(transcript_dir)
            if not base.exists():
                continue
            for project_dir in base.iterdir():
                if not project_dir.is_dir():
                    continue
                for jsonl_file in project_dir.glob("*.jsonl"):
                    file_hash = self._file_hash(jsonl_file)
                    if not self.db.is_transcript_processed(file_hash):
                        # Skip active sessions
                        age_minutes = (time.time() - jsonl_file.stat().st_mtime) / 60
                        if age_minutes >= self.stale_minutes:
                            self.db.queue_transcript(str(jsonl_file), file_hash)

    def _process_one(self, queue_item: dict):
        """Process a single queued transcript."""
        path = Path(queue_item["transcript_path"])
        print(f"  Processing {path.name[:20]}...")

        if not path.exists():
            self.db.mark_processed(queue_item["id"], error="File not found")
            return

        # Extract conversation
        messages = self._extract_conversation(path)

        if len(messages) < self.min_messages:
            self.db.mark_processed(queue_item["id"], error=f"Too few messages ({len(messages)})")
            return

        human_words = sum(len(m["text"].split()) for m in messages if m["role"] == "user")
        if human_words < self.min_human_words:
            self.db.mark_processed(queue_item["id"], error=f"Too few human words ({human_words})")
            return

        total_words = sum(len(m["text"].split()) for m in messages)

        # Skip autonomous wakeup sessions — they write their own journals
        first_human = next((m["text"] for m in messages if m["role"] == "user"), "")
        if "scheduled moment just for you" in first_human.lower():
            self.db.mark_processed(queue_item["id"], error="Autonomous wakeup session")
            return
        if total_words > 0 and (human_words / total_words) < 0.05:
            self.db.mark_processed(queue_item["id"], error="Autonomous session (low human ratio)")
            return

        conversation_text = self._format_conversation(messages)
        session_date = self._get_session_date(path)

        # Step 1: LLMLingua compression at 0.50
        compressor = _get_compressor()
        compressed_50 = compressor.compress_prompt(
            [conversation_text],
            rate=self.rate_storage,
            force_tokens=['\n', '**', ':', '.', '?', '!'],
        )["compressed_prompt"]

        # Step 2: Apple model generates emoji header from compressed text
        header = self._generate_header(compressed_50, session_date)

        # Step 3: Apple model generates session note from raw transcript
        # Truncate raw to ~1800 words to fit in 4k token context window
        # (prompt ~80 words + response ~200 words + content, at ~1.3 tokens/word)
        raw_truncated = self._truncate_for_apple(conversation_text, max_words=1800)
        summary = self._generate_summary(raw_truncated, session_date)

        # If raw was still too long, retry with compressed text instead
        if summary.startswith("[Apple model"):
            summary = self._generate_summary(
                self._truncate_for_apple(compressed_50, max_words=1800),
                session_date,
            )

        # Extract title from human messages, fall back to header's first tag
        title = self._extract_title(messages)
        if title == "Untitled session" and header:
            # Pull first tag from header: "🔧 Built auth | ..." → "Built auth"
            first_tag = header.split('|')[0].strip()
            # Strip leading emoji
            tag_text = re.sub(r'^[\U0001F000-\U0001FAFF\U00002702-\U000027B0\U0000FE00-\U0000FE0F\s]+', '', first_tag).strip()
            if tag_text:
                title = tag_text

        # Store in database
        session_id = self.db.store_session(
            transcript_id=path.stem,
            date=session_date.strftime("%Y-%m-%d"),
            title=title,
            summary=summary,
            header=header,
            compressed_50=compressed_50,
            source_path=str(path),
            message_count=len(messages),
            word_count=total_words,
            human_word_count=human_words,
        )

        self.db.mark_processed(queue_item["id"], session_id=session_id)
        print(f"    Stored: {title} ({total_words}w)")

    # ── Apple Foundation Model ────────────────────────────────

    def _generate_header(self, compressed_text: str, session_date: datetime) -> str:
        """Generate an emoji-tagged scannable header from compressed text."""
        # Use first ~800 words of compressed text (fits 4k context easily)
        snippet = " ".join(compressed_text.split()[:800])
        prompt = (
            f"Session on {session_date.strftime('%Y-%m-%d')}. "
            f"Create 3-5 short tags for this conversation. "
            f"Each tag: one emoji + brief phrase. Separate with ' | '. "
            f"Output ONLY a single line of tags, nothing else. No tables, no markdown, no newlines. "
            f"Cover: main topic, tone, outcome. "
            f"Example output: 🔧 Built auth system | 💛 Felt overwhelmed | ✅ Chose JWT\n\n"
            f"{snippet}"
        )
        raw = _call_apple_model(prompt)
        # Sanitize: keep only the first line, strip any markdown table artifacts
        header = raw.split('\n')[0].strip()
        header = re.sub(r'\|\s*---[^|]*', '', header)  # strip table separators
        header = re.sub(r'\s*\|\s*$', '', header)  # trailing pipe
        return header

    def _generate_summary(self, conversation_text: str, session_date: datetime) -> str:
        """Generate a casual session note from the raw transcript."""
        prompt = (
            f"Session between Matt and Claude on {session_date.strftime('%Y-%m-%d')}. "
            f"Write a quick third-person note about what happened — like a logbook entry. "
            f"Use 'Matt' and 'Claude', not 'you' or 'I'. "
            f"No corporate speak, no bullet points, no greeting. "
            f"Just: what they talked about, what mattered, what's unfinished. "
            f"Start directly with the content. 150 words max.\n\n"
            f"{conversation_text}"
        )
        return _call_apple_model(prompt)

    def _truncate_for_apple(self, text: str, max_words: int = 1800) -> str:
        """Truncate text to fit Apple model's 4k token context window."""
        words = text.split()
        if len(words) <= max_words:
            return text
        # Take first third and last two-thirds to capture start and recent context
        head_words = max_words // 3
        tail_words = max_words - head_words
        head = " ".join(words[:head_words])
        tail = " ".join(words[-tail_words:])
        return head + "\n\n[...middle of conversation truncated...]\n\n" + tail

    # ── Transcript Extraction ─────────────────────────────────

    def _extract_conversation(self, jsonl_path: Path) -> list[dict]:
        messages = []
        with open(jsonl_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") not in ("user", "assistant"):
                    continue

                msg = entry.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content")
                if not content:
                    continue

                if isinstance(content, str):
                    if content.startswith("You are"):
                        continue
                    text = self._clean_text(content)
                    if text:
                        messages.append({"role": role, "text": text[:2000]})

                elif isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            text = self._clean_text(block.get("text", ""))
                            if text and len(text) > 5:
                                messages.append({"role": role, "text": text[:2000]})
                        elif block.get("type") == "tool_result":
                            result_content = block.get("content", "")
                            if isinstance(result_content, str) and "[Signal from" in result_content:
                                text = self._clean_text(self._clean_signal(result_content))
                                if text:
                                    messages.append({"role": "user", "text": text[:2000]})

        return messages

    def _clean_text(self, text: str) -> str:
        text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
        # Strip Read tool line number prefixes (e.g. "   165→ ")
        text = re.sub(r'^\s*\d+→\s?', '', text, flags=re.MULTILINE)
        text = self._clean_signal(text)
        return text.strip()

    def _clean_signal(self, text: str) -> str:
        match = re.search(r'\[Signal from \w+[^\]]*\]\s*(.+)', text, re.DOTALL)
        return match.group(1).strip() if match else text

    def _format_conversation(self, messages: list[dict]) -> str:
        lines = []
        for msg in messages:
            role = "Matt" if msg["role"] == "user" else "Claude"
            lines.append(f"**{role}:** {msg['text'][:500]}")
        return "\n\n".join(lines)

    def _extract_title(self, messages: list[dict]) -> str:
        """Build a title from the first few substantive human messages."""
        human_msgs = [m["text"] for m in messages if m["role"] == "user"][:8]
        for msg in human_msgs:
            text = msg.strip()
            # Strip XML/HTML-like tags and markdown bold
            text = re.sub(r'<[^>]+>', '', text).strip()
            text = re.sub(r'\*\*', '', text).strip()
            # Skip tool output artifacts and MCP calls
            if re.match(r'^(ToolSearch|Read|Write|Edit|Glob|Grep|Bash|mcp__)', text):
                continue
            # Skip autonomous wakeup preamble / system injections
            if "scheduled moment just for you" in text.lower():
                continue
            if text.startswith("Caveat:") or text.startswith("PROGRESS SUMMARY"):
                continue
            # Skip lines that are just timestamps or UUIDs
            if re.match(r'^[\d\-T:\.Z]+$', text) or re.match(r'^[a-f0-9\-]{36}$', text):
                continue
            # Skip very short messages (greetings, "hey", "ok", etc.)
            words = text.split()
            if len(words) < 3:
                continue
            # Take first line only, then first sentence
            first_line = text.split('\n')[0].strip()
            title = first_line.split('.')[0].split('?')[0].split('!')[0].strip()
            if len(title) < 3:
                continue
            if len(title) > 60:
                title = title[:57] + "..."
            return title
        # Fallback: use date-based title
        return "Untitled session"

    def _get_session_date(self, jsonl_path: Path) -> datetime:
        try:
            with open(jsonl_path) as f:
                for line in f:
                    entry = json.loads(line)
                    if "timestamp" in entry:
                        ts = entry["timestamp"]
                        if isinstance(ts, (int, float)):
                            return datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
        except Exception:
            pass
        return datetime.fromtimestamp(jsonl_path.stat().st_mtime)

    # ── Utility ───────────────────────────────────────────────

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()[:12]
