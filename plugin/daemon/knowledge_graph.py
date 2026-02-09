"""Knowledge graph for Memorable daemon.

Extracts entities, topics, and decisions from session notes into a flat
structure optimised for the 3B model's relevance checking. Each entry
has a file:line address so hints can point Claude to specific locations.

The graph lives at ~/.memorable/data/kg.json and is rebuilt periodically
by the daemon (or manually via CLI).

Structure:
{
    "built_at": "...",
    "entities": {
        "name": {
            "type": "person|project|tool|concept",
            "mentions": 5,
            "facts": ["fact with address", ...],
            "last_seen": "2026-02-09"
        }
    },
    "topics": [
        {"text": "...", "addr": "file:line", "date": "2026-02-09", "type": "decision|rejection|summary|open_thread"}
    ]
}
"""

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
KG_PATH = DATA_DIR / "kg.json"
MANUAL_KG_PATH = Path.home() / "claude-memory" / "knowledge-graph" / "memory.jsonl"

# Map claude-mem entity types to daemon's simpler type system
_ENTITY_TYPE_MAP = {
    "person": "person",
    "cat": "person",
    "AI_companion": "person",
    "project": "project",
    "tech": "project",
    "AI_tool": "project",
    "event": "event",
    "trip": "event",
    "session-note": "event",
    "concept": "concept",
    "relationship": "concept",
    "canonical_fact": "concept",
    "preference": "preference",
    "decision": "decision",
    "health": "health",
    "life_status": "health",
    "place": "place",
    "company": "place",
}


def build_knowledge_graph(notes_dir: Path | None = None) -> dict:
    """Build a knowledge graph from all session notes.

    Returns the graph dict. Also writes it to KG_PATH.
    """
    notes_dir = notes_dir or (DATA_DIR / "notes")

    entities: dict[str, dict] = {}
    topics: list[dict] = []

    for jsonl_file in sorted(notes_dir.glob("*.jsonl")):
        try:
            lines = jsonl_file.read_text().strip().split("\n")
        except OSError:
            continue

        for line_num, raw_line in enumerate(lines, start=1):
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            note = entry.get("note", "")
            ts = entry.get("first_ts", entry.get("ts", ""))
            date = ts[:10] if ts else ""
            addr = f"{jsonl_file}:{line_num}"

            # Parse sections
            sections = _parse_sections(note)

            # Extract from Summary
            summary = sections.get("Summary", "").strip()
            if summary:
                # Keep first 200 chars as a topic
                topics.append({
                    "text": summary[:200],
                    "addr": addr,
                    "date": date,
                    "type": "summary",
                })

            # Extract decisions
            for line in _extract_bullets(sections.get("Decisions", "")):
                topics.append({
                    "text": f"DECIDED: {line[:150]}",
                    "addr": addr,
                    "date": date,
                    "type": "decision",
                })

            # Extract rejections
            for line in _extract_bullets(sections.get("Rejections", "")):
                topics.append({
                    "text": f"REJECTED: {line[:150]}",
                    "addr": addr,
                    "date": date,
                    "type": "rejection",
                })

            # Extract open threads
            for line in _extract_bullets(sections.get("Open Threads", "")):
                topics.append({
                    "text": f"OPEN: {line[:150]}",
                    "addr": addr,
                    "date": date,
                    "type": "open_thread",
                })

            # Extract people
            people_text = sections.get("People & Life", "")
            _extract_people(people_text, entities, addr, date)

            # Extract projects/tools from Technical Context
            tech_text = sections.get("Technical Context", "")
            _extract_tech_entities(tech_text, summary, entities, addr, date)

    # Load manually curated entities from claude-mem KG
    _load_manual_entities(entities, topics)

    # Sort topics by date (newest first) and cap
    topics.sort(key=lambda t: t.get("date", ""), reverse=True)

    graph = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "entity_count": len(entities),
        "topic_count": len(topics),
        "entities": entities,
        "topics": topics,
    }

    # Write to disk
    KG_PATH.parent.mkdir(parents=True, exist_ok=True)
    KG_PATH.write_text(json.dumps(graph, indent=2))
    logger.info("Knowledge graph built: %d entities, %d topics -> %s", len(entities), len(topics), KG_PATH)

    return graph


def load_topic_index(max_topics: int = 100) -> str:
    """Load the KG and format a topic index string for the relevance prompt.

    This replaces the daemon's crude _load_topic_index().
    Returns a newline-separated list of topics with addresses.
    """
    if not KG_PATH.exists():
        return ""

    try:
        graph = json.loads(KG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return ""

    lines = []

    # Add entity summaries with their most recent fact
    entities = graph.get("entities", {})
    # Sort by mentions descending, take top entities
    sorted_ents = sorted(
        entities.items(),
        key=lambda x: x[1].get("mentions", 0),
        reverse=True,
    )
    for name, info in sorted_ents:
        if info.get("mentions", 0) < 2:
            continue
        # Skip "Matt" — he's always relevant, wastes a slot
        if name == "Matt":
            continue
        facts = info.get("facts", [])
        last_fact = facts[-1] if facts else {}
        addr = last_fact.get("addr", "")
        etype = info.get("type", "entity")
        snippet = last_fact.get("text", "")[:80] if last_fact else ""
        entry = f"{addr} [{info.get('last_seen', '')}] {etype.upper()}: {name}"
        if snippet:
            entry += f" — {snippet}"
        lines.append(entry)

    entity_count = len(lines)

    # Add recent topics — decisions and open threads are highest value
    topics = graph.get("topics", [])
    # Prioritise: decisions > open_threads > rejections > summaries
    # Within same priority, prefer recent dates
    priority = {"decision": 0, "open_thread": 1, "rejection": 2, "summary": 3}
    scored = sorted(
        topics,
        key=lambda t: (
            priority.get(t.get("type", ""), 4),
            "" if t.get("date") else "z",  # undated last
            t.get("date", ""),  # within priority, sort by date (will reverse)
        ),
    )

    remaining = max_topics - entity_count
    for topic in scored[:remaining]:
        addr = topic.get("addr", "")
        date = topic.get("date", "")
        text = topic.get("text", "")
        lines.append(f"{addr} [{date}] {text}")

    return "\n".join(lines[:max_topics])


class RelevanceFilter:
    """Fast local pre-filter for relevance checking.

    Uses two tiers before calling the remote 3B model:
    1. Keyword match: does the message contain a known entity name? (~0.004ms)
    2. Embedding similarity: cosine similarity against topic embeddings (~2.5ms)

    Only messages that pass either tier get sent to the 3B model.
    """

    def __init__(self, similarity_threshold: float = 0.35, max_candidates: int = 10):
        self.similarity_threshold = similarity_threshold
        self.max_candidates = max_candidates

        self._entity_names: set[str] = set()
        self._entity_map: dict[str, dict] = {}  # name -> entity info
        self._topic_texts: list[str] = []
        self._topic_entries: list[dict] = []
        self._topic_embeddings: list[list[float]] = []
        self._embedder = None
        self._tagger = None
        self._NL = None  # Reference to NaturalLanguage module

        self._init_apple_nl()

    def _init_apple_nl(self):
        """Initialize Apple NaturalLanguage framework (embeddings + tagger)."""
        try:
            import NaturalLanguage as NL
            self._NL = NL

            # Sentence embeddings
            self._embedder = NL.NLEmbedding.sentenceEmbeddingForLanguage_("en")
            if self._embedder:
                logger.info("Apple sentence embeddings loaded (%d dims)", self._embedder.dimension())
            else:
                logger.warning("Apple sentence embeddings not available for English")

            # NER tagger for discovering unknown names
            self._tagger = NL.NLTagger.alloc().initWithTagSchemes_([NL.NLTagSchemeNameType])
            if self._tagger:
                logger.info("Apple NLTagger (NER) loaded")

        except ImportError:
            logger.warning("NaturalLanguage framework not available — install pyobjc-framework-NaturalLanguage")

    def load_from_kg(self):
        """Load entities and topics from the KG file, build embedding index."""
        if not KG_PATH.exists():
            return

        try:
            graph = json.loads(KG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return

        # Load entity names for keyword matching
        entities = graph.get("entities", {})
        self._entity_names = set()
        self._entity_map = {}
        for name, info in entities.items():
            if name == "Matt":
                continue
            if info.get("mentions", 0) >= 1:
                self._entity_names.add(name.lower())
                self._entity_map[name.lower()] = {
                    "name": name,
                    "type": info.get("type", "entity"),
                    "facts": info.get("facts", []),
                }

        # Load topics for embedding matching
        topics = graph.get("topics", [])
        self._topic_texts = []
        self._topic_entries = []
        self._topic_embeddings = []

        if self._embedder:
            for topic in topics[:500]:  # Cap to keep embedding index manageable
                text = topic.get("text", "")
                if not text:
                    continue
                vec = self._embedder.vectorForString_(text)
                if vec:
                    self._topic_texts.append(text)
                    self._topic_entries.append(topic)
                    self._topic_embeddings.append(list(vec))

        logger.info(
            "RelevanceFilter loaded: %d entity names, %d topic embeddings",
            len(self._entity_names), len(self._topic_embeddings),
        )

    def check(self, message: str) -> Optional[dict]:
        """Check if a message has potential relevance to known context.

        Three-tier local pre-filter:
          Tier 0: Keyword match against known entity names (~0.004ms)
          Tier 0.5: NLTagger NER for unknown names (~0.5ms)
          Tier 1: Embedding similarity against topic index (~2.5ms)

        Returns a dict with matched info if relevant, None if not.
        The dict contains:
          - trigger: "keyword" | "ner" | "embedding"
          - entities: list of matched entity names (for keyword)
          - unknown_names: list of names NER found that aren't in KG
          - topics: list of (score, topic_entry) tuples (for embedding)
          - candidates_text: formatted string for the 3B model prompt
        """
        if len(message) < 8:
            return None

        result = {
            "trigger": None,
            "entities": [],
            "unknown_names": [],
            "topics": [],
            "candidates_text": "",
        }
        candidates = []

        # Tier 0: Keyword match against known entity names
        msg_lower = message.lower()
        matched_entities = []
        for name_lower in self._entity_names:
            if name_lower in msg_lower:
                info = self._entity_map[name_lower]
                matched_entities.append(info)
                for fact in info.get("facts", [])[-3:]:
                    candidates.append(
                        f"{fact.get('addr', '')} [{fact.get('date', '')}] "
                        f"{info['type'].upper()}: {info['name']} — {fact.get('text', '')[:100]}"
                    )

        if matched_entities:
            result["trigger"] = "keyword"
            result["entities"] = [e["name"] for e in matched_entities]

        # Tier 0.5: NLTagger NER — find names not in KG
        if self._tagger and self._NL:
            unknown = self._extract_unknown_names(message)
            if unknown:
                result["unknown_names"] = unknown
                if not result["trigger"]:
                    result["trigger"] = "ner"

        # Tier 1: Embedding similarity
        if self._embedder and self._topic_embeddings:
            msg_vec = self._embedder.vectorForString_(message)
            if msg_vec:
                msg_vec = list(msg_vec)
                scored = []
                for i, tvec in enumerate(self._topic_embeddings):
                    sim = _cosine_sim(msg_vec, tvec)
                    if sim >= self.similarity_threshold:
                        scored.append((sim, i))

                scored.sort(reverse=True)
                for sim, idx in scored[:self.max_candidates]:
                    entry = self._topic_entries[idx]
                    result["topics"].append((sim, entry))
                    candidates.append(
                        f"{entry.get('addr', '')} [{entry.get('date', '')}] {entry.get('text', '')[:120]}"
                    )
                    if not result["trigger"]:
                        result["trigger"] = "embedding"

        if not result["trigger"]:
            return None

        # Deduplicate candidates by address
        seen_addrs = set()
        unique = []
        for c in candidates:
            addr = c.split(" [")[0] if " [" in c else c
            if addr not in seen_addrs:
                seen_addrs.add(addr)
                unique.append(c)

        result["candidates_text"] = "\n".join(unique[:self.max_candidates])
        return result

    def _extract_unknown_names(self, message: str) -> list[str]:
        """Use NLTagger to find person/org/place names not in the KG.

        Returns names that Apple NER recognises but our entity list doesn't contain.
        These are potential new entities worth investigating.
        """
        NL = self._NL
        self._tagger.setString_(message)

        names = []

        def handler(tag, token_range, stop):
            if tag and str(tag) in ("PersonalName", "OrganizationName"):
                text = self._tagger.string()[token_range.location:token_range.location + token_range.length]
                if text and len(text) > 1:
                    names.append(text)

        self._tagger.enumerateTagsInRange_unit_scheme_options_usingBlock_(
            (0, len(message)),
            NL.NLTokenUnitWord,
            NL.NLTagSchemeNameType,
            NL.NLTaggerOmitWhitespace | NL.NLTaggerOmitPunctuation,
            handler,
        )

        # Filter to only names NOT already in the KG
        unknown = []
        for name in names:
            if name.lower() not in self._entity_names and name.lower() != "matt":
                unknown.append(name)

        return unknown


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _parse_sections(note: str) -> dict[str, str]:
    """Parse markdown note into {section_name: content} dict."""
    sections = {}
    current = None
    current_lines = []

    for line in note.split("\n"):
        if line.strip().startswith("## "):
            if current:
                sections[current] = "\n".join(current_lines)
            current = line.strip()[3:].strip()
            current_lines = []
        elif current is not None:
            current_lines.append(line)

    if current:
        sections[current] = "\n".join(current_lines)

    return sections


def _extract_bullets(text: str) -> list[str]:
    """Extract markdown bullet lines, stripping leading '- '."""
    bullets = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("- "):
            # Strip bold markers for cleaner text
            clean = line[2:].strip()
            clean = re.sub(r'\*\*(.*?)\*\*', r'\1', clean)
            if clean:
                bullets.append(clean)
    return bullets


def _extract_people(text: str, entities: dict, addr: str, date: str):
    """Extract person entities from People & Life section."""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Match **Name** — description or **Name**: description
        match = re.match(r'\*\*(.*?)\*\*\s*[—:\-]\s*(.*)', line)
        if not match:
            # Also match - **Name** — description
            match = re.match(r'-\s*\*\*(.*?)\*\*\s*[—:\-]\s*(.*)', line)
        if not match:
            continue

        name = match.group(1).strip().rstrip(':')
        desc = match.group(2).strip()

        if not name or len(name) > 40:
            continue

        # Skip things that aren't actual person/entity names
        # (DeepSeek sometimes bolds section themes as if they're people)
        if not _looks_like_name(name):
            continue

        # Skip compound names like "Madoka and Sophie" — too ambiguous
        if " and " in name and len(name.split(" and ")) == 2:
            # Try to split into two names
            parts = [p.strip() for p in name.split(" and ")]
            for part in parts:
                if _looks_like_name(part):
                    _add_person_entity(
                        _normalise_name(part), desc, entities, addr, date
                    )
            continue

        name = _normalise_name(name)
        _add_person_entity(name, desc, entities, addr, date)


def _add_person_entity(name: str, desc: str, entities: dict, addr: str, date: str):
    """Add or update a person entity with a fact."""
    if not name:
        return

    if name not in entities:
        entities[name] = {
            "type": "person",
            "mentions": 0,
            "facts": [],
            "last_seen": "",
        }

    entities[name]["mentions"] += 1
    if date > entities[name].get("last_seen", ""):
        entities[name]["last_seen"] = date

    if desc and len(desc) > 5:
        fact = {"text": desc[:200], "addr": addr, "date": date}
        existing_texts = {f["text"] for f in entities[name]["facts"]}
        if desc[:200] not in existing_texts:
            entities[name]["facts"].append(fact)
            if len(entities[name]["facts"]) > 10:
                entities[name]["facts"] = entities[name]["facts"][-10:]


def _looks_like_name(name: str) -> bool:
    """Check if a string looks like a person/entity name vs a description."""
    if not name or not name[0].isupper():
        return False
    # Names are usually 1-4 words
    words = name.split()
    if len(words) > 4:
        return False
    # Filter out common non-name patterns from DeepSeek
    skip_patterns = [
        "memory", "characteristics", "social", "contraction", "context",
        "technical", "agent team", "session", "thread", "architecture",
        "system", "framework", "discussion", "conversation", "summary",
        # Group/generic labels
        "trip", "crew", "users", "colleagues", "friends", "parents",
        "seller", "file", "themes", "mentioned", "removed", "dysmorphia",
        "people", "close", "group", "routine", "events", "family",
        "client", "recruiter", "brother", "sister",
    ]
    lower = name.lower()
    if any(p in lower for p in skip_patterns):
        return False
    # Comma-separated lists like "Sarah, Ben, Tess, Hamish" aren't single names
    if ',' in name:
        return False
    return True


def _extract_tech_entities(text: str, summary: str, entities: dict, addr: str, date: str):
    """Extract project/tool entities from Technical Context and Summary."""
    # Known project patterns — canonical_name: regex
    project_patterns = [
        ("Memorable", r'\b[Mm]emorable\b', "project"),
        ("claude-mem", r'\bclaude-mem\b', "project"),
        ("Moltbook", r'\b[Mm]oltbook\b', "project"),
        ("Signal Claude", r'\bSignal Claude\b', "project"),
        ("Nano Banana", r'\b[Nn]ano [Bb]anana\b', "project"),
    ]

    combined = f"{text}\n{summary}"

    for canonical, pattern, etype in project_patterns:
        if re.search(pattern, combined):
            if canonical not in entities:
                entities[canonical] = {
                    "type": etype,
                    "mentions": 0,
                    "facts": [],
                    "last_seen": "",
                }

            entities[canonical]["mentions"] += 1
            if date > entities[canonical].get("last_seen", ""):
                entities[canonical]["last_seen"] = date


def _normalise_name(name: str) -> str:
    """Normalise person names to handle aliases and duplicates."""
    # Map known aliases
    aliases = {
        "Matt Kennelly": "Matt",
        "Matt K": "Matt",
        "Matthew": "Matt",
        "Jassi": "Jaskaran",
        "Jak": "Jaskaran",
        "Signal-Claude": "Signal Claude",
        "Sophie": "Sophia",
        "Matt's mum": "Trish",
        "Matt's Mum": "Trish",
        "Mum": "Trish",
    }

    # Check if name contains a parenthetical alias
    paren_match = re.match(r'(.*?)\s*\((.*?)\)', name)
    if paren_match:
        name = paren_match.group(1).strip()

    return aliases.get(name, name)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    graph = build_knowledge_graph()
    print(f"\nEntities: {graph['entity_count']}")
    print(f"Topics: {graph['topic_count']}")

    print("\n--- Top entities (by mentions) ---")
    sorted_entities = sorted(
        graph["entities"].items(),
        key=lambda x: x[1]["mentions"],
        reverse=True,
    )
    for name, info in sorted_entities[:20]:
        print(f"  {name} ({info['type']}, {info['mentions']} mentions, last: {info['last_seen']})")
        for fact in info["facts"][:3]:
            print(f"    - {fact['text'][:80]}")

    print(f"\n--- Sample topic index (first 20 lines) ---")
    index = load_topic_index(max_topics=20)
    print(index)
