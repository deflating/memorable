"""Transcript processing pipeline for Memorable.

Reads .jsonl transcripts, filters with heuristics, compresses
with LLMLingua-2 at two tiers (0.50 storage, 0.20 skeleton),
stores results in SQLite.

No cloud APIs, no LLM calls. Fully local, deterministic compression.
"""

import json
import hashlib
import re
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


class TranscriptProcessor:
    def __init__(self, config: Config):
        self.config = config
        self.db = MemorableDB(Path(config["db_path"]))
        self.min_messages = config.get("min_messages", 15)
        self.min_human_words = config.get("min_human_words", 100)
        self.stale_minutes = config.get("stale_minutes", 15)
        self.rate_storage = config.get("compression_rate_storage", 0.50)
        self.rate_skeleton = config.get("compression_rate_skeleton", 0.20)

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

        # Check human ratio (skip autonomous sessions)
        total_words = sum(len(m["text"].split()) for m in messages)
        if total_words > 0 and (human_words / total_words) < 0.05:
            self.db.mark_processed(queue_item["id"], error="Autonomous session")
            return

        conversation_text = self._format_conversation(messages)
        session_date = self._get_session_date(path)

        # Compress at two tiers
        compressor = _get_compressor()

        compressed_50 = compressor.compress_prompt(
            [conversation_text],
            rate=self.rate_storage,
            force_tokens=['\n', '**', ':', '.', '?', '!'],
        )["compressed_prompt"]

        compressed_20 = compressor.compress_prompt(
            [conversation_text],
            rate=self.rate_skeleton,
            force_tokens=['\n', '**', ':'],
        )["compressed_prompt"]

        # Generate a title from the first few human messages
        title = self._extract_title(messages)

        # Store in database
        session_id = self.db.store_session(
            transcript_id=path.stem,
            date=session_date.strftime("%Y-%m-%d"),
            title=title,
            compressed_50=compressed_50,
            skeleton_20=compressed_20,
            source_path=str(path),
            message_count=len(messages),
            word_count=total_words,
            human_word_count=human_words,
        )

        self.db.mark_processed(queue_item["id"], session_id=session_id)
        ratio_50 = len(compressed_50.split()) / max(total_words, 1)
        print(f"    Stored: {title} ({total_words}w → {len(compressed_50.split())}w @ {ratio_50:.0%})")

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
        human_msgs = [m["text"] for m in messages if m["role"] == "user"][:5]
        # Take the first human message that's more than a greeting
        for msg in human_msgs:
            text = msg.strip()
            # Skip very short messages (greetings, "hey", "ok", etc.)
            if len(text.split()) < 4:
                continue
            # Truncate to first sentence or 60 chars
            title = text.split('.')[0].split('?')[0].split('!')[0]
            if len(title) > 60:
                title = title[:57] + "..."
            return title
        # Fallback: just use the first human message
        if human_msgs:
            return human_msgs[0][:60]
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
