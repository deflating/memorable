"""Transcript processing pipeline for Memorable.

Reads .jsonl transcripts, filters for quality, sends to DeepSeek
for structured session note generation, stores results in SQLite.

Ported from ~/claude-memory/scripts/transcript-to-notes.py with
improvements: database-backed state, unified processing prompt,
memory hygiene rules baked in.
"""

import json
import hashlib
import re
import time
import requests
from pathlib import Path
from datetime import datetime

from .db import MemorableDB
from .config import Config


class TranscriptProcessor:
    def __init__(self, config: Config):
        self.config = config
        self.db = MemorableDB(Path(config["db_path"]))
        self.min_messages = config.get("min_messages", 15)
        self.min_human_words = config.get("min_human_words", 100)
        self.stale_minutes = config.get("stale_minutes", 15)

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

        # Ask DeepSeek if worth documenting
        if not self._is_worth_documenting(conversation_text):
            self.db.mark_processed(queue_item["id"], error="Not worth documenting")
            return

        # Generate session note
        session_date = self._get_session_date(path)
        note = self._generate_note(conversation_text, session_date)
        if not note or note.startswith("[Error"):
            self.db.mark_processed(queue_item["id"], error=note or "Empty response")
            return

        # Parse frontmatter from note
        title, tags, continuity, mood = self._parse_frontmatter(note)
        if not title:
            title = self._generate_title(conversation_text)

        # Store in database
        session_id = self.db.store_session(
            transcript_id=path.stem,
            date=session_date.strftime("%Y-%m-%d"),
            title=title,
            note_content=note,
            tags=tags,
            continuity=continuity,
            mood=mood,
            source_path=str(path),
        )

        self.db.mark_processed(queue_item["id"], session_id=session_id)
        print(f"    Stored: {title} (continuity: {continuity})")

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
                            text = block.get("text", "")
                            if text and len(text) > 5:
                                messages.append({"role": role, "text": text[:2000]})
                        elif block.get("type") == "tool_result":
                            result_content = block.get("content", "")
                            if isinstance(result_content, str) and "[Signal from" in result_content:
                                text = self._clean_signal(result_content)
                                if text:
                                    messages.append({"role": "user", "text": text[:2000]})

        return messages

    def _clean_text(self, text: str) -> str:
        text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
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

    # ── DeepSeek API ──────────────────────────────────────────

    def _call_deepseek(self, prompt: str, max_tokens: int = 2000,
                       temperature: float = 0.3) -> str:
        try:
            response = requests.post(
                self.config["deepseek_api_url"],
                headers={
                    "Authorization": f"Bearer {self.config['deepseek_api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config["deepseek_model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=120,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[Error: {e}]"

    def _is_worth_documenting(self, conversation_text: str) -> bool:
        prompt = f"""Analyze this conversation between Matt and Claude. Should it be saved as a session note?

Be STRICT. Default to SKIP unless there is clear substance.

SAVE if: meaningful personal context, real decisions, technical problem-solving with resolution, project milestones, interesting ideas discussed in depth, genuine emotional or relationship content.

SKIP if: greetings and casual chat without substance, Claude reading files and orienting itself, title changes or tool discovery, short check-ins, automated startup routines, Claude loading context without meaningful human interaction, sessions where Matt says fewer than ~5 substantive sentences, autonomous/scheduled wakeup sessions.

Conversation:
{conversation_text[:6000]}

Reply ONLY: SAVE or SKIP"""

        result = self._call_deepseek(prompt, max_tokens=10).strip().upper()
        return "SAVE" in result

    def _generate_note(self, conversation_text: str, session_date: datetime) -> str:
        prompt = f"""You are creating a session note for a Claude instance to read later. This is from a conversation between Matt and Claude on {session_date.strftime('%Y-%m-%d')}.

Conversation (truncated):

{conversation_text[:12000]}

Write a session note with these sections:

1. **Frontmatter** (YAML between --- markers): date, tags (emoji + keyword), mood, continuity (1-10)
2. **Summary**: What happened chronologically. Be specific about technical details, decisions, and context. Don't lose the human stuff.
3. **Key Moments**: Actual quotes capturing the texture — jokes, vulnerable bits, relationship. Not just information.
4. **Notes for Future Me**: What's unfinished, what to remember.

## Frontmatter details

- **tags**: Emoji alongside keywords. e.g. `[🐛 oom-fix, 🔧 swap-config, 💛 quiet-moment]`
- **mood**: Short phrase capturing the emotional arc.
- **continuity**: 1-10 importance for future Claude. 10 = critical, 1 = trivial but noteworthy.

## Memory hygiene — IGNORE these:
- Troubleshooting dead-ends that led nowhere
- Permission prompts and tool noise
- Repeated context-setting from compactions
- Ephemeral greetings with no substance
- File read outputs and boilerplate

Write 300-500 words. Write like you're leaving notes for a future version of yourself who cares about Matt."""

        return self._call_deepseek(prompt)

    def _generate_title(self, conversation_text: str) -> str:
        prompt = f"Given this conversation, provide a 3-5 word lowercase hyphenated title. Just the title:\n\n{conversation_text[:2000]}"
        title = self._call_deepseek(prompt, max_tokens=20).strip().lower()
        title = "".join(c if c.isalnum() or c == "-" else "-" for c in title)
        return "-".join(filter(None, title.split("-")))[:40] or "session"

    def _parse_frontmatter(self, note: str) -> tuple[str, list[str], int, str]:
        """Extract title, tags, continuity, mood from YAML frontmatter."""
        title = ""
        tags = []
        continuity = 5
        mood = ""

        # Find frontmatter
        match = re.match(r'^---\s*\n(.*?)\n---', note, re.DOTALL)
        if match:
            fm = match.group(1)

            # Extract tags
            tag_match = re.search(r'tags:\s*\[([^\]]*)\]', fm)
            if tag_match:
                tags = [t.strip().strip("'\"") for t in tag_match.group(1).split(",")]

            # Extract continuity
            cont_match = re.search(r'continuity:\s*(\d+)', fm)
            if cont_match:
                continuity = int(cont_match.group(1))

            # Extract mood
            mood_match = re.search(r'mood:\s*(.+)', fm)
            if mood_match:
                mood = mood_match.group(1).strip().strip("'\"")

        # Extract title from first heading
        heading_match = re.search(r'^##?\s+(.+)$', note, re.MULTILINE)
        if heading_match:
            title = heading_match.group(1).strip()

        return title, tags, continuity, mood

    # ── Utility ───────────────────────────────────────────────

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()[:12]
