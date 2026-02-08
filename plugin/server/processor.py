"""Transcript processing pipeline for Memorable.

Reads .jsonl transcripts, filters with heuristics, summarizes with
Haiku via `claude -p` (uses existing Claude Code subscription),
then writes a JSON session file to ~/.memorable/data/sessions/.

Files are the source of truth. No database writes here.
"""

import json
import hashlib
import logging
import re
import subprocess
import time
from pathlib import Path
from datetime import datetime

from .config import Config

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
SESSIONS_DIR = DATA_DIR / "sessions"


def _summarize_with_haiku(fact_sheet: str, model: str = "haiku") -> str:
    """Generate a narrative session summary from a structured fact sheet."""
    system_prompt = (
        "You are a session note writer. You receive a FACT SHEET and output "
        "EXACTLY ONE PARAGRAPH of narrative prose. Nothing else.\n\n"
        "IMPORTANT: You are NOT making a judgement call about whether this session "
        "is worth documenting. That decision has already been made. Every fact sheet "
        "you receive MUST get a summary. Never refuse or say a session isn't worth it.\n\n"
        "RULES:\n"
        "- Past tense, third person. The user's name is Matt.\n"
        "- Be SPECIFIC: mention file names, commands, error messages from the facts.\n"
        "- Capture the ARC: what was attempted, what happened, how it ended.\n"
        "- If quotes show emotion (frustration, humor, excitement), weave it in.\n"
        "- Start directly with the action: 'Matt wanted to...' or 'Matt asked about...'\n"
        "- If Signal messages, this was a chat via Signal messaging.\n"
        "- NO preamble, NO commentary, NO questions, NO bullet points, NO headers.\n"
        "- Do NOT address the user. Do NOT ask clarifying questions.\n"
        "- Output ONLY the summary paragraph. 4-8 sentences."
    )
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model, "--no-session-persistence",
             "--system-prompt", system_prompt, "--tools", ""],
            input=fact_sheet,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            # Strip preamble lines — Haiku sometimes narrates before summarizing
            lines = text.split('\n')
            while lines and (
                lines[0].strip() == "" or
                any(lines[0].lower().startswith(p) for p in [
                    "i'll ", "i will ", "here's ", "here is ", "let me ", "sure",
                    "i see the session", "i'm a transcript", "i'm the transcript",
                    "i've reviewed", "i see the entire",
                    "looking at the full session", "now i understand",
                    "based on the ", "no observation",
                    "i appreciate", "i need to ", "i want to ",
                    "before i ", "i'm looking at",
                    "perfect", "okay", "ok,", "right",
                    "---", "##", "**",
                ])
            ):
                lines.pop(0)
            text = '\n'.join(lines).strip()
            if '\n\n' in text:
                paragraphs = text.split('\n\n')
                matt_paras = [p for p in paragraphs
                              if p.strip().startswith('Matt ') and len(p.strip()) > 100]
                if matt_paras:
                    text = matt_paras[0].strip()
            if not text:
                text = result.stdout.strip()
            if '\n1.' in text or '\n- ' in text or '\n**' in text:
                paragraphs = text.split('\n\n')
                narrative = [p for p in paragraphs
                             if not p.strip().startswith(('1.', '2.', '- ', '**', '#'))
                             and len(p.strip()) > 100]
                if narrative:
                    text = max(narrative, key=len)
            return text
        error = result.stderr.strip() or f"Exit code {result.returncode}"
        return f"[Haiku summary failed: {error}]"
    except subprocess.TimeoutExpired:
        return "[Haiku summary failed: timeout after 120s]"
    except FileNotFoundError:
        return "[Haiku summary failed: claude CLI not found]"


def _slugify(text: str, max_len: int = 60) -> str:
    """Convert text to a URL/filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text).strip('-')
    return text[:max_len].rstrip('-')


class TranscriptProcessor:
    def __init__(self, config: Config):
        self.config = config
        self.min_messages = config.get("min_messages", 15)
        self.min_human_words = config.get("min_human_words", 100)
        self.stale_minutes = config.get("stale_minutes", 15)
        self.summary_model = config.get("summary_model", "haiku")
        # Ensure output directories exist
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def process_all(self):
        """Scan transcript directories, process new ones."""
        pending = self._find_unprocessed_transcripts()
        logger.info(f"Found {len(pending)} unprocessed transcript(s)")

        for jsonl_path in pending:
            try:
                self._process_one(jsonl_path)
            except Exception as e:
                logger.error(f"Error processing {jsonl_path.name}: {e}")

    def _find_unprocessed_transcripts(self) -> list[Path]:
        """Find transcripts that don't have a corresponding session file yet."""
        # Build set of already-processed transcript IDs
        processed_ids = set()
        for session_file in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(session_file.read_text())
                if "id" in data:
                    processed_ids.add(data["id"])
            except Exception:
                continue

        pending = []
        for transcript_dir in self.config["transcript_dirs"]:
            base = Path(transcript_dir)
            if not base.exists():
                continue
            for project_dir in base.iterdir():
                if not project_dir.is_dir():
                    continue
                for jsonl_file in project_dir.glob("*.jsonl"):
                    # Skip active sessions
                    age_minutes = (time.time() - jsonl_file.stat().st_mtime) / 60
                    if age_minutes < self.stale_minutes:
                        continue
                    # Skip already-processed
                    if jsonl_file.stem in processed_ids:
                        continue
                    pending.append(jsonl_file)

        return pending

    def _process_one(self, path: Path):
        """Process a single transcript → write session JSON file."""
        logger.info(f"Processing {path.name[:20]}")

        if not path.exists():
            logger.warning(f"File not found: {path}")
            return

        # Extract session data from JSONL
        from .notes import extract_session_data, build_fact_sheet
        note_data = extract_session_data(path)

        messages = note_data["messages"]
        human_words = note_data["user_word_count"]
        total_words = note_data["total_word_count"]

        if len(messages) < self.min_messages:
            logger.info(f"Skipping {path.name}: too few messages ({len(messages)})")
            return

        if human_words < self.min_human_words:
            logger.info(f"Skipping {path.name}: too few human words ({human_words})")
            return

        # Skip autonomous wakeup sessions
        first_human = next((m["text"] for m in messages if m["role"] == "user"), "")
        if "scheduled moment just for you" in first_human.lower():
            logger.info(f"Skipping {path.name}: autonomous wakeup session")
            return
        if total_words > 0 and (human_words / total_words) < 0.05:
            logger.info(f"Skipping {path.name}: autonomous session (low human ratio)")
            return

        # Skip observer/watcher sessions
        first_assistant = next((m["text"] for m in messages if m["role"] == "assistant"), "")
        observer_phrases = [
            "i'm ready to observe", "i'll observe", "i'll monitor",
            "i need to observe", "ready to observe and record",
            "monitor the primary session", "observe the primary",
        ]
        if any(first_assistant.lower().startswith(p) or p in first_assistant.lower()[:200]
               for p in observer_phrases):
            logger.info(f"Skipping {path.name}: observer/watcher session")
            return

        # Already processed? (check by transcript ID = filename stem)
        transcript_id = path.stem
        for existing in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(existing.read_text())
                if data.get("id") == transcript_id:
                    logger.info(f"Already processed: {transcript_id}")
                    return
            except Exception:
                continue

        session_date = self._get_session_date(path)

        # Build fact sheet and summarize with Haiku
        fact_sheet = build_fact_sheet(note_data)
        summary_text = _summarize_with_haiku(fact_sheet, model=self.summary_model)
        logger.info(f"Generated summary: {summary_text[:80]}...")

        # Extract title
        title = self._extract_title(messages)

        # Write session JSON file
        session_data = {
            "id": transcript_id,
            "date": session_date.strftime("%Y-%m-%d"),
            "title": title,
            "summary": summary_text,
            "message_count": len(messages),
            "word_count": total_words,
            "human_word_count": human_words,
            "source_transcript": path.name,
            "processed_at": datetime.now().isoformat(),
        }

        # Filename: date-slugified-title.json
        slug = _slugify(title)
        filename = f"{session_date.strftime('%Y-%m-%d')}-{slug}.json"
        output_path = SESSIONS_DIR / filename

        # Handle collision
        if output_path.exists():
            filename = f"{session_date.strftime('%Y-%m-%d')}-{slug}-{transcript_id[:8]}.json"
            output_path = SESSIONS_DIR / filename

        output_path.write_text(json.dumps(session_data, indent=2) + "\n")
        logger.info(f"Wrote session: {output_path.name} ({total_words}w)")

    def _extract_title(self, messages: list[dict]) -> str:
        """Build a title from the first few substantive human messages."""
        human_msgs = [m["text"] for m in messages if m["role"] == "user"][:8]
        for msg in human_msgs:
            text = msg.strip()
            if text.startswith("Signal: "):
                text = text[len("Signal: "):]
            text = re.sub(r'<[^>]+>', '', text).strip()
            text = re.sub(r'\*\*', '', text).strip()
            if re.match(r'^(ToolSearch|Read|Write|Edit|Glob|Grep|Bash|mcp__)', text):
                continue
            if "scheduled moment just for you" in text.lower():
                continue
            if text.startswith("Caveat:") or text.startswith("PROGRESS SUMMARY"):
                continue
            if "Claude's Full Response" in text or "Full Response to User" in text:
                continue
            if text.startswith("Claude:") or text.startswith("User:"):
                continue
            if re.match(r'^[\d\-T:\.Z]+$', text) or re.match(r'^[a-f0-9\-]{36}$', text):
                continue
            words = text.split()
            if len(words) < 3:
                continue
            first_line = text.split('\n')[0].strip()
            title = first_line.split('.')[0].split('?')[0].split('!')[0].strip()
            if len(title) < 3:
                continue
            if len(title) > 60:
                title = title[:57] + "..."
            return title
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

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()[:12]
