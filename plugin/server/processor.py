"""Transcript processing pipeline for Memorable.

Reads .jsonl transcripts, filters with heuristics, summarizes with
Haiku via `claude -p` (uses existing Claude Code subscription),
then extracts structured metadata using lightweight NLP tools:

- GLiNER: zero-shot named entity recognition (~200MB on disk)
- Apple Foundation Model: emoji tag headers only

The Haiku summary is the primary memory; metadata provides the
searchable index.
"""

import json
import hashlib
import logging
import re
import subprocess
import time
from pathlib import Path
from datetime import datetime

from .db import MemorableDB
from .config import Config

logger = logging.getLogger(__name__)


# Lazy-loaded extractors
_gliner_model = None


def _summarize_with_haiku(fact_sheet: str, model: str = "haiku") -> str:
    """Generate a narrative session summary from a structured fact sheet.

    Instead of sending raw conversation text, we send a pre-built fact sheet
    containing: opening request, actions taken, errors, user quotes, and ending.
    This produces specific, narrative summaries instead of generic paragraphs.
    """
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
            # If Haiku went off-script (multiple paragraphs with questions/bullets),
            # try to extract just the narrative paragraph
            text = '\n'.join(lines).strip()
            # If multiple paragraphs remain and one starts with "Matt",
            # use that — it's likely the actual summary
            if '\n\n' in text:
                paragraphs = text.split('\n\n')
                matt_paras = [p for p in paragraphs
                              if p.strip().startswith('Matt ') and len(p.strip()) > 100]
                if matt_paras:
                    text = matt_paras[0].strip()
            if not text:
                text = result.stdout.strip()
            # If output has bullet points or numbered lists, it went off-script.
            # Find the longest non-bullet paragraph and use that.
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


def _get_gliner():
    global _gliner_model
    if _gliner_model is None:
        try:
            from gliner import GLiNER
            _gliner_model = GLiNER.from_pretrained("urchade/gliner_small-v2.1")
        except Exception as e:
            print(f"    GLiNER model unavailable (first run requires internet): {e}")
            return None
    return _gliner_model


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
        self.db = MemorableDB(
            Path(config["db_path"]),
            sync_url=config.get("sync_url", ""),
            auth_token=config.get("sync_auth_token", ""),
        )
        self.min_messages = config.get("min_messages", 15)
        self.min_human_words = config.get("min_human_words", 100)
        self.stale_minutes = config.get("stale_minutes", 15)
        self.summary_model = config.get("summary_model", "haiku")

    def process_all(self):
        """Scan transcript directories, queue new files, process pending."""
        self._scan_for_new_transcripts()
        pending = self.db.get_pending_transcripts()
        logger.info(f"Processing {len(pending)} pending transcript(s)")

        for item in pending:
            try:
                self._process_one(item)
            except Exception as e:
                logger.error(f"Error processing {item['transcript_path']}: {e}")
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
        logger.info(f"Processing {path.name[:20]}")

        if not path.exists():
            self.db.mark_processed(queue_item["id"], error="File not found")
            return

        # Extract session data from JSONL (mechanical, no LLM)
        from .notes import extract_session_data
        note_data = extract_session_data(path)

        messages = note_data["messages"]
        human_words = note_data["user_word_count"]
        total_words = note_data["total_word_count"]

        if len(messages) < self.min_messages:
            self.db.mark_processed(queue_item["id"], error=f"Too few messages ({len(messages)})")
            return

        if human_words < self.min_human_words:
            self.db.mark_processed(queue_item["id"], error=f"Too few human words ({human_words})")
            return

        # Skip autonomous wakeup sessions — they write their own journals
        first_human = next((m["text"] for m in messages if m["role"] == "user"), "")
        if "scheduled moment just for you" in first_human.lower():
            self.db.mark_processed(queue_item["id"], error="Autonomous wakeup session")
            return
        if total_words > 0 and (human_words / total_words) < 0.05:
            self.db.mark_processed(queue_item["id"], error="Autonomous session (low human ratio)")
            return

        # Skip agent team / teammate sessions — spawned by Task tool
        agent_phrases = [
            "you are on the", "you are a testing agent",
            "you are the architect", "you are the skeptic",
            "you are the reliability", "you are the product",
            "you are the performance", "your name is",
            "check tasklist to find your assigned task",
        ]
        if any(p in first_human.lower()[:300] for p in agent_phrases):
            self.db.mark_processed(queue_item["id"], error="Agent team session")
            return

        # Skip observer/watcher sessions — they just watch other sessions
        first_assistant = next((m["text"] for m in messages if m["role"] == "assistant"), "")
        observer_phrases = [
            "i'm ready to observe", "i'll observe", "i'll monitor",
            "i need to observe", "ready to observe and record",
            "monitor the primary session", "observe the primary",
        ]
        if any(first_assistant.lower().startswith(p) or p in first_assistant.lower()[:200]
               for p in observer_phrases):
            self.db.mark_processed(queue_item["id"], error="Observer/watcher session")
            return

        session_date = self._get_session_date(path)

        # Step 1: Build fact sheet and send to Haiku for narrative summary
        from .notes import build_fact_sheet, compose_session_note
        fact_sheet = build_fact_sheet(note_data)
        summary_text = _summarize_with_haiku(fact_sheet, model=self.summary_model)
        logger.info(f"Generated summary: {summary_text[:80]}...")

        # Step 2: Apple model generates emoji header from summary
        header = self._generate_header(summary_text, session_date)

        # Step 3: Extract structured metadata (GLiNER) from fact sheet
        metadata_text = f"{summary_text}\n{fact_sheet[:1000]}"
        metadata = self._extract_metadata(metadata_text)
        metadata_json = json.dumps(metadata)

        # Extract title from human messages, fall back to header's first tag
        title = self._extract_title(messages)
        if title == "Untitled session" and header:
            # Pull first tag from header: "🔧 Built auth | ..." → "Built auth"
            first_tag = header.split('|')[0].strip()
            # Strip leading emoji
            tag_text = re.sub(r'^[\U0001F000-\U0001FAFF\U00002702-\U000027B0\U0000FE00-\U0000FE0F\s]+', '', first_tag).strip()
            if tag_text:
                title = tag_text

        # Step 4: Generate structured session notes from JSONL
        note_result = {}
        try:
            note_result = compose_session_note(
                data=note_data,
                summary_text=summary_text,
                header=header,
                date=session_date.strftime("%Y-%m-%d"),
                title=title,
                db=self.db,
            )
            logger.info(f"Generated notes: {len(note_result.get('note_content', ''))} chars, "
                       f"{len(note_data.get('files_touched', []))} files")
        except Exception as e:
            logger.warning(f"Notes generation error: {e}")

        # Compute embedding for the summary (for pre-embedded search)
        summary_embedding = None
        try:
            from .embeddings import embed_text
            summary_embedding = embed_text(summary_text)
        except Exception as e:
            logger.warning(f"Failed to embed summary: {e}")

        # Store in database with transaction (wrap store_session + mark_processed atomically)
        try:
            session_id = self.db.store_session(
                transcript_id=path.stem,
                date=session_date.strftime("%Y-%m-%d"),
                title=title,
                summary=summary_text,
                header=header,
                compressed_50=note_result.get("note_content", ""),
                metadata=metadata_json,
                source_path=str(path),
                message_count=len(messages),
                word_count=total_words,
                human_word_count=human_words,
                note_content=note_result.get("note_content", ""),
                tags=note_result.get("tags", "[]"),
                mood=note_result.get("mood", ""),
                continuity=note_result.get("continuity", 5),
                summary_embedding=summary_embedding,
            )
            self.db.mark_processed(queue_item["id"], session_id=session_id)
            logger.info(f"Stored session: {title} ({total_words}w, session_id={session_id})")
        except Exception as e:
            logger.error(f"Failed to store session: {e}")
            raise

        # Step 5: KG extraction from session summary
        try:
            from .kg import extract_from_session
            kg_result = extract_from_session(
                session_text=summary_text,
                session_title=title,
                session_header=header,
                session_metadata=metadata_json,
                db=self.db,
            )
            if kg_result["entities_added"] or kg_result["relationships_added"]:
                logger.info(f"KG: +{kg_result['entities_added']} entities, "
                           f"+{kg_result['relationships_added']} relationships")
        except Exception as e:
            logger.warning(f"KG extraction error: {e}")

    # ── Apple Foundation Model (headers only) ───────────────

    def _generate_header(self, compressed_text: str, session_date: datetime) -> str:
        """Generate an emoji-tagged scannable header from compressed text."""
        snippet = " ".join(compressed_text.split()[:800])
        prompt = (
            f"Create 3-5 tags for this conversation. "
            f"Each tag: one emoji then 2-4 words. Separate with ' | '. "
            f"ONE line only. No markdown. No tables.\n"
            f"Example: 🔧 Built auth system | 💛 Felt overwhelmed | ✅ Chose JWT\n\n"
            f"{snippet}"
        )
        raw = _call_apple_model(prompt)
        # Sanitize: keep only the first line, strip any markdown table artifacts
        header = raw.split('\n')[0].strip()
        header = re.sub(r'\|\s*---[^|]*', '', header)  # strip table separators
        header = re.sub(r'\s*\|\s*$', '', header)  # trailing pipe
        return header

    # ── Metadata Extraction (GLiNER) ─────────────────────────

    def _extract_metadata(self, conversation_text: str) -> dict:
        """Extract structured metadata from conversation using lightweight NLP."""
        metadata = {}

        # GLiNER entity extraction
        try:
            model = _get_gliner()
            if model is None:
                raise RuntimeError("GLiNER model not loaded")
            labels = ["person", "technology", "software tool", "hardware",
                       "operating system", "command"]
            words = conversation_text.split()
            chunk_size = 384
            entity_counts: dict[tuple[str, str], int] = {}
            skip_ents = {"user", "claude", "option", "options"}

            for i in range(0, len(words), chunk_size):
                chunk = " ".join(words[i:i + chunk_size])
                try:
                    results = model.predict_entities(chunk, labels, threshold=0.3)
                    for ent in results:
                        text = ent["text"].strip()
                        if text.lower() in skip_ents or len(text) < 2:
                            continue
                        key = (text, ent["label"])
                        entity_counts[key] = entity_counts.get(key, 0) + 1
                except Exception:
                    continue

            # Group by label, sorted by frequency
            entities_by_type: dict[str, list] = {}
            for (text, label), count in sorted(entity_counts.items(), key=lambda x: -x[1]):
                entities_by_type.setdefault(label, []).append(
                    {"name": text, "count": count}
                )
            metadata["entities"] = entities_by_type
        except Exception as e:
            metadata["entities"] = {}
            metadata["entities_error"] = str(e)

        return metadata

    # ── Transcript Extraction (moved to notes.py) ────────────────

    def _extract_title(self, messages: list[dict]) -> str:
        """Build a title from the first few substantive human messages."""
        human_msgs = [m["text"] for m in messages if m["role"] == "user"][:8]
        for msg in human_msgs:
            text = msg.strip()
            # Strip "Signal: " prefix from forwarded messages
            if text.startswith("Signal: "):
                text = text[len("Signal: "):]
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
            # Skip transcript artifacts and context recovery noise
            if "Claude's Full Response" in text or "Full Response to User" in text:
                continue
            if text.startswith("Claude:") or text.startswith("User:"):
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
