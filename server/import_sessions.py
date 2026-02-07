#!/usr/bin/env python3
"""Import existing markdown session notes into Memorable's database.

Reads session note files from ~/claude-memory/sessions/, parses
frontmatter and content, and inserts into the SQLite database.

Usage:
    python -m server.import_sessions
    python -m server.import_sessions --dry-run
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.db import MemorableDB
from server.config import Config


def parse_session_note(filepath: Path) -> dict | None:
    """Parse a session note markdown file into structured data."""
    content = filepath.read_text()
    if not content.strip():
        return None

    result = {
        "source_path": str(filepath),
        "note_content": content,
        "tags": [],
        "continuity": 5,
        "mood": "",
        "title": "",
        "date": "",
    }

    # Extract date from filename (YYYY-MM-DD-title.md)
    date_match = re.match(r'(\d{4}-\d{2}-\d{2})', filepath.name)
    if date_match:
        result["date"] = date_match.group(1)

    # Extract transcript_id from filename (minus date and extension)
    result["transcript_id"] = filepath.stem

    # Parse YAML frontmatter
    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)

        # Tags
        tag_match = re.search(r'tags:\s*\[([^\]]*)\]', fm)
        if tag_match:
            result["tags"] = [t.strip().strip("'\"") for t in tag_match.group(1).split(",") if t.strip()]

        # Continuity
        cont_match = re.search(r'continuity:\s*(\d+)', fm)
        if cont_match:
            result["continuity"] = int(cont_match.group(1))

        # Mood
        mood_match = re.search(r'mood:\s*(.+)', fm)
        if mood_match:
            result["mood"] = mood_match.group(1).strip().strip("'\"")

        # Date from frontmatter (override filename)
        date_match = re.search(r'date:\s*(\d{4}-\d{2}-\d{2})', fm)
        if date_match:
            result["date"] = date_match.group(1)

    # Extract title — skip generic headings like "Summary", "Key Moments"
    generic_headings = {"summary", "key moments", "notes for future me", "voice"}
    for heading_match in re.finditer(r'^##?\s+(.+)$', content, re.MULTILINE):
        candidate = heading_match.group(1).strip()
        if candidate.lower() not in generic_headings:
            result["title"] = candidate
            break

    if not result["title"]:
        # Derive title from filename: 2026-02-04-signal-bridge-setup.md → Signal Bridge Setup
        name_part = re.sub(r'^\d{4}-\d{2}-\d{2}-?', '', filepath.stem)
        result["title"] = name_part.replace("-", " ").title() if name_part else filepath.stem

    return result


def import_sessions(dry_run: bool = False):
    config = Config()
    memory_dir = Path(config["memory_dir"])
    sessions_dir = memory_dir / "sessions"

    if not sessions_dir.exists():
        print(f"Sessions directory not found: {sessions_dir}")
        return

    db = MemorableDB(Path(config["db_path"]))

    imported = 0
    skipped = 0
    errors = 0

    for md_file in sorted(sessions_dir.glob("**/*.md")):
        if md_file.name == "CLAUDE.md":
            continue

        parsed = parse_session_note(md_file)
        if not parsed:
            skipped += 1
            continue

        if not parsed["date"]:
            print(f"  Skip (no date): {md_file.name}")
            skipped += 1
            continue

        if dry_run:
            print(f"  Would import: {parsed['title']} ({parsed['date']}, c:{parsed['continuity']})")
            imported += 1
            continue

        try:
            db.store_session(
                transcript_id=parsed["transcript_id"],
                date=parsed["date"],
                title=parsed["title"],
                note_content=parsed["note_content"],
                tags=parsed["tags"],
                continuity=parsed["continuity"],
                mood=parsed["mood"],
                source_path=parsed["source_path"],
            )
            imported += 1
            print(f"  Imported: {parsed['title']} ({parsed['date']})")
        except Exception as e:
            errors += 1
            print(f"  Error importing {md_file.name}: {e}")

    action = "Would import" if dry_run else "Imported"
    print(f"\n{action}: {imported} | Skipped: {skipped} | Errors: {errors}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no changes will be made\n")
    import_sessions(dry_run=dry_run)
