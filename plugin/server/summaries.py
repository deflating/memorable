"""Rolling summary generation for Memorable.

Generates a 5-day rolling summary from recent sessions. Called on
session_start if the existing summary is stale (>24h old). Uses
Haiku for speed and cost efficiency.

Output: stored in DB rolling_summaries table AND written as markdown
to ~/.memorable/summaries/ for external use (Notion sync, etc).
"""

import json
import time
from datetime import datetime
from pathlib import Path

from .db import MemorableDB
from .llm import call_llm
from .config import Config

# How old a summary can be before we regenerate (seconds)
STALE_THRESHOLD = 86400  # 24 hours


def is_summary_stale(db: MemorableDB) -> bool:
    """Check if the rolling summary needs regeneration."""
    latest = db.get_latest_rolling_summary()
    if not latest:
        return True
    age = time.time() - (latest.get("created_at") or 0)
    return age > STALE_THRESHOLD


def generate_rolling_summary(config: Config, db: MemorableDB) -> str | None:
    """Generate a 5-day rolling summary from recent sessions.

    Returns the summary text, or None if generation failed or
    there are no sessions to summarize.
    """
    sessions = db.get_recent_sessions(days=5, limit=20)
    if not sessions:
        return None

    # Build the prompt from session data
    session_blocks = []
    for s in sessions:
        block = f"- {s['date']} | {s['title']}"
        if s.get("header"):
            block += f"\n  Tags: {s['header']}"
        if s.get("summary"):
            block += f"\n  Keywords: {s['summary'][:200]}"

        # Include metadata if available
        if s.get("metadata") and s["metadata"] != "{}":
            try:
                meta = json.loads(s["metadata"]) if isinstance(s["metadata"], str) else s["metadata"]
                entities = meta.get("entities", {})
                if entities:
                    ent_names = []
                    for etype, elist in entities.items():
                        for e in (elist if isinstance(elist, list) else []):
                            name = e.get("name", e) if isinstance(e, dict) else str(e)
                            ent_names.append(name)
                    if ent_names:
                        block += f"\n  Entities: {', '.join(ent_names[:10])}"
            except (json.JSONDecodeError, TypeError):
                pass

        word_count = s.get("word_count", 0)
        msg_count = s.get("message_count", 0)
        if word_count:
            block += f"\n  ({msg_count} messages, {word_count} words)"

        session_blocks.append(block)

    sessions_text = "\n\n".join(session_blocks)
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = (
        f"Summarize the last 5 days of activity from these {len(sessions)} sessions. "
        f"Today is {today}.\n\n"
        f"Write a concise rolling summary (3-6 paragraphs) covering:\n"
        f"1. What was worked on and accomplished\n"
        f"2. Key decisions made\n"
        f"3. Current state / what's in progress\n\n"
        f"Be specific — use project names, file names, tools mentioned. "
        f"Write in third person ('The user worked on...'). No bullet points.\n\n"
        f"Sessions:\n{sessions_text}"
    )

    system = (
        "You generate concise rolling summaries of development activity. "
        "Focus on facts: what changed, what was decided, what's next. "
        "No filler, no encouragement, no questions."
    )

    summary = call_llm(prompt, system=system, model="haiku")
    if not summary:
        return None

    # Store in DB
    db.store_rolling_summary(
        content=summary,
        summary_type="5day",
        days_covered=5,
        session_count=len(sessions),
    )

    # Write markdown file to disk
    summaries_dir = Path.home() / ".memorable" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    filename = f"rolling-5day-{today}.md"
    filepath = summaries_dir / filename

    md_content = (
        f"# Rolling Summary — {today}\n\n"
        f"*{len(sessions)} sessions over the last 5 days*\n\n"
        f"{summary}\n"
    )
    filepath.write_text(md_content)

    return summary


def get_or_generate_summary(config: Config, db: MemorableDB) -> str | None:
    """Get the current rolling summary, generating if stale.

    This is the main entry point — called from session_start.
    """
    if not is_summary_stale(db):
        latest = db.get_latest_rolling_summary()
        if latest:
            return latest["content"]

    # Stale or missing — generate a new one
    return generate_rolling_summary(config, db)
