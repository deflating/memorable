"""Build the oracle's context document.

Assembles session notes, knowledge graph entities, and seed files into a
single markdown document that becomes the oracle's system prompt. The 7B
model holds this in its context window and answers questions against it.

Usage:
    python3 -m oracle.build_context          # build context only
    python3 -m oracle.build_context --warm   # build + warm KV cache
"""

import argparse
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
NOTES_DIR = DATA_DIR / "notes"
MANUAL_KG_PATH = Path.home() / "claude-memory" / "knowledge-graph" / "memory.jsonl"
CONTEXT_PATH = DATA_DIR / "oracle-context.md"

# How many recent notes to include in full
RECENT_NOTES_FULL = 50

ORACLE_INSTRUCTIONS = """You are Matt's memory oracle. You hold his complete session history and knowledge graph in your context.

When asked a question:
1. Search through the context below for relevant information
2. Quote specific details: dates, decisions, people mentioned, technical choices
3. If you find multiple relevant sessions, mention all of them
4. If you don't know or the information isn't in your context, say "I don't have information about that in my current context"

Be concise. Return facts, not essays. Include dates when available."""


def build_oracle_context() -> str:
    """Build the full oracle context document.

    Returns the assembled markdown string. Also writes it to
    ~/.memorable/data/oracle-context.md for inspection.
    """
    parts = [ORACLE_INSTRUCTIONS, "\n---\n"]

    # --- Seeds ---
    parts.append("# Identity\n")
    for name in ("matt", "claude", "now"):
        path = SEEDS_DIR / f"{name}.md"
        if path.exists():
            try:
                content = path.read_text().strip()
                parts.append(content)
                parts.append("")
            except OSError:
                pass

    # --- Session Notes ---
    notes = _load_all_notes()
    notes.sort(key=lambda n: n.get("date", ""), reverse=True)

    if notes:
        recent = notes[:RECENT_NOTES_FULL]
        older = notes[RECENT_NOTES_FULL:]

        parts.append("# Recent Sessions\n")
        for i, note in enumerate(recent):
            date = note.get("date", "unknown")
            text = note.get("note", "")
            parts.append(f"## Session [{date}]\n")
            parts.append(text)
            parts.append("")

        if older:
            parts.append("# Older Sessions (summaries only)\n")
            for note in older:
                date = note.get("date", "unknown")
                summary = _extract_summary(note.get("note", ""))
                if summary:
                    parts.append(f"- [{date}] {summary}")
            parts.append("")

    # --- Knowledge Graph ---
    kg_entities = _load_manual_kg()
    if kg_entities:
        parts.append("# Knowledge Graph\n")

        # Group by type
        by_type: dict[str, list[dict]] = {}
        for entity in kg_entities:
            etype = entity.get("entityType", "other")
            by_type.setdefault(etype, []).append(entity)

        for etype in sorted(by_type.keys()):
            parts.append(f"## {etype}\n")
            for entity in by_type[etype]:
                name = entity.get("name", "")
                observations = entity.get("observations", [])
                # Cap at 3 observations, 150 chars each
                obs_text = "; ".join(o[:150] for o in observations[:3])
                parts.append(f"- **{name}**: {obs_text}")
            parts.append("")

    context = "\n".join(parts)

    # Write to disk for inspection
    CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_PATH.write_text(context)
    logger.info(
        "Oracle context built: %d chars, %d notes (%d full, %d summary), %d KG entities -> %s",
        len(context),
        len(notes),
        min(len(notes), RECENT_NOTES_FULL),
        max(0, len(notes) - RECENT_NOTES_FULL),
        len(kg_entities),
        CONTEXT_PATH,
    )

    return context


def rebuild_and_warm(oracle_url: str = "http://192.168.68.58:8400/v1/chat/completions"):
    """Rebuild context and warm the KV cache with a dummy query."""
    context = build_oracle_context()

    logger.info("Warming KV cache (%d chars)...", len(context))
    start = time.monotonic()

    try:
        from .oracle_client import OracleClient
        client = OracleClient(url=oracle_url)
        client.ask("ping", context_override=context, max_tokens=5)
        elapsed = time.monotonic() - start
        logger.info("KV cache warmed in %.1fs", elapsed)
    except Exception as e:
        logger.warning("KV cache warmup failed (oracle may not be running): %s", e)


def _load_all_notes() -> list[dict]:
    """Load all session notes from JSONL files."""
    notes = []
    if not NOTES_DIR.exists():
        return notes

    for jsonl_file in NOTES_DIR.glob("*.jsonl"):
        try:
            lines = jsonl_file.read_text().strip().split("\n")
        except OSError:
            continue

        for raw_line in lines:
            if not raw_line.strip():
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            note = entry.get("note", "")
            if not note:
                continue

            ts = entry.get("first_ts", entry.get("ts", ""))
            date = ts[:10] if ts else ""

            notes.append({
                "date": date,
                "note": note,
                "session": entry.get("session", ""),
            })

    return notes


def _extract_summary(note: str) -> str:
    """Extract the first non-header content line from a note."""
    for line in note.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:200]
    return ""


def _load_manual_kg() -> list[dict]:
    """Load entities from the manual knowledge graph."""
    if not MANUAL_KG_PATH.exists():
        return []

    entities = []
    try:
        raw = MANUAL_KG_PATH.read_text().strip()
    except OSError:
        return []

    for raw_line in raw.split("\n"):
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "entity":
            continue

        name = entry.get("name", "").strip()
        if not name or name in ("Matt", "Matt Kennelly"):
            continue

        entities.append(entry)

    return entities


def main():
    parser = argparse.ArgumentParser(description="Build oracle context document")
    parser.add_argument("--warm", action="store_true", help="Also warm the KV cache")
    parser.add_argument("--oracle-url", type=str,
                        default="http://192.168.68.58:8400/v1/chat/completions",
                        help="MLX server URL")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.warm:
        rebuild_and_warm(args.oracle_url)
    else:
        context = build_oracle_context()
        print(f"Context built: {len(context)} chars -> {CONTEXT_PATH}")


if __name__ == "__main__":
    main()
