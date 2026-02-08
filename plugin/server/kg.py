"""Knowledge graph extraction pipeline for Memorable.

Extracts entities and relationships from session summaries using:
1. Haiku — structured JSON extraction from natural language (primary)
2. GLiNER — zero-shot NER as a complementary pass
3. NLGazetteer — fast lookup of already-known entities
4. Lightweight filtering — structural checks on entity names

The key insight: extract from session summaries (natural language written
by Haiku), NOT from raw tool output (code, grep results, file contents).
This produces dramatically better entity candidates.
"""

import json
import logging
import re
from pathlib import Path

from .db import MemorableDB
from .config import Config
from .llm import call_llm_json

logger = logging.getLogger(__name__)

# ── Entity & Relationship Types ─────────────────────────────

ENTITY_TYPES = {
    "person", "project", "technology", "organization",
    "file", "concept", "tool", "service", "language",
}

RELATIONSHIP_TYPES = {
    "uses", "builds", "created", "owns", "depends_on",
    "part_of", "works_with", "configured_in", "deployed_on",
    "related_to",
}

# Compact noise filter — only truly generic words that Haiku might still produce.
# Much smaller than before because Haiku on session summaries produces far less garbage.
_NOISE_ENTITIES = {
    # Generic programming terms that aren't specific named things
    "tool", "file", "code", "database", "function", "method", "class",
    "module", "script", "command", "server", "client", "system", "process",
    "plugin", "session", "framework", "library", "package", "schema",
    "pipeline", "daemon", "handler", "watcher", "processor",
    # Entity type names (LLMs sometimes echo these)
    "person", "project", "technology", "organization", "concept", "service",
    # Generic action/state words
    "change", "update", "delete", "search", "build", "test", "error",
    # Memorable-internal terms
    "knowledge graph", "observations", "entities", "relationships",
    "watcher log", "kg extraction",
    # Common CLI tools (not specific enough for KG)
    "git", "python", "npm", "bash",
}

# ── NLGazetteer — Known Entity Lookup ───────────────────────

_gazetteer = None
_gazetteer_data = None
_nl_ns = None


def _load_nl():
    """Load NaturalLanguage framework once."""
    global _nl_ns
    if _nl_ns is None:
        import objc
        _nl_ns = {}
        objc.loadBundle(
            'NaturalLanguage', _nl_ns,
            '/System/Library/Frameworks/NaturalLanguage.framework',
        )
    return _nl_ns


def build_gazetteer(db: MemorableDB) -> dict:
    """Build a gazetteer dictionary from all KG entities.

    Returns {entity_type: [name1, name2, ...]} and caches the
    NLGazetteer object for fast lookups.
    """
    global _gazetteer, _gazetteer_data

    entities = db.query_kg(min_priority=4, limit=5000)
    data = {}
    for e in entities:
        etype = e.get("type", "concept")
        name = e.get("name", "")
        if name:
            data.setdefault(etype, []).append(name)

    if data == _gazetteer_data and _gazetteer is not None:
        return data

    _gazetteer_data = data

    if not data:
        _gazetteer = None
        return data

    try:
        ns = _load_nl()
        NLGazetteer = ns['NLGazetteer']
        result = NLGazetteer.alloc().initWithDictionary_language_error_(
            data, 'en', None
        )
        if isinstance(result, tuple):
            gaz, err = result
            if err:
                print(f"  Gazetteer error: {err}")
                _gazetteer = None
            else:
                _gazetteer = gaz
        else:
            _gazetteer = result
    except Exception as e:
        print(f"  Gazetteer init failed: {e}")
        _gazetteer = None

    return data


def gazetteer_lookup(text: str) -> list[dict]:
    """Find known entities in text using the cached gazetteer."""
    if _gazetteer is None or not _gazetteer_data:
        return []

    found = []
    text_lower = text.lower()
    for etype, names in _gazetteer_data.items():
        for name in names:
            if name.lower() in text_lower:
                label = _gazetteer.labelForString_(name)
                if label:
                    found.append({"name": name, "type": label})
    return found


# ── GLiNER — Zero-Shot NER ──────────────────────────────────

_gliner_model = None


def _get_gliner():
    """Lazy-load GLiNER model."""
    global _gliner_model
    if _gliner_model is None:
        try:
            from gliner import GLiNER
            _gliner_model = GLiNER.from_pretrained("urchade/gliner_small-v2.1")
        except ImportError:
            pass
    return _gliner_model


def gliner_extract(text: str, threshold: float = 0.4) -> list[dict]:
    """Extract entities from text using GLiNER zero-shot NER.

    Returns list of {"name": ..., "type": ...}
    """
    model = _get_gliner()
    if model is None:
        return []

    labels = ["person", "technology", "project", "programming language",
              "company", "framework", "hardware"]
    # GLiNER label → our entity type mapping
    type_map = {
        "person": "person",
        "technology": "technology",
        "project": "project",
        "programming language": "language",
        "company": "organization",
        "framework": "technology",
        "hardware": "technology",
    }

    try:
        # Process in chunks (GLiNER has limited context)
        words = text.split()
        chunk_size = 384
        seen = set()
        entities = []

        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i:i + chunk_size])
            try:
                results = model.predict_entities(chunk, labels, threshold=threshold)
                for ent in results:
                    name = ent["text"].strip()
                    name_lower = name.lower()
                    if name_lower in seen or len(name) < 2:
                        continue
                    seen.add(name_lower)
                    entities.append({
                        "name": name,
                        "type": type_map.get(ent["label"], "concept"),
                    })
            except Exception:
                continue
        return entities
    except Exception:
        return []


# ── Haiku Extraction — Primary Pipeline ─────────────────────

_HAIKU_EXTRACTION_PROMPT = """\
Extract named entities and relationships from this session summary.

EXTRACT ONLY specific, named things:
- Real people (by name)
- Named projects or products (e.g. "Memorable", "Signal Claude", not "the project")
- Specific technologies, frameworks, libraries (e.g. "SQLite", "React", "FastAPI", not "database" or "framework")
- Programming languages (e.g. "Python", "JavaScript", "Rust")
- Organizations/companies (e.g. "Anthropic", "Apple", "Google")
- Specific hardware (e.g. "Mac Mini M4", "Raspberry Pi")
- Key concepts unique to this person's work (e.g. "SDAM", not "memory" or "AI")

DO NOT extract:
- Generic words (database, server, plugin, file, tool, code, system)
- Internal code identifiers, variable names, file paths
- CLI commands (git, npm, python, bash)

Session summary:
{text}

Return ONLY valid JSON with no markdown fences:
{{"entities": [{{"name": "...", "type": "person|project|technology|organization|concept|language", "description": "1-line description"}}], "relationships": [{{"source": "...", "predicate": "uses|builds|created|owns|depends_on|part_of|works_with|configured_in|deployed_on|related_to", "target": "..."}}]}}\
"""


def haiku_extract(text: str) -> dict:
    """Extract entities and relationships from text using Haiku.

    This is the primary extraction method — Haiku understands natural language
    well enough to identify real named entities vs generic words.

    Returns {"entities": [...], "relationships": [...]}
    """
    if len(text.strip()) < 50:
        return {"entities": [], "relationships": []}

    prompt = _HAIKU_EXTRACTION_PROMPT.format(text=text[:3000])
    result = call_llm_json(
        prompt,
        system="Extract named entities from text. Return only valid JSON. No preamble.",
        model="haiku",
    )

    if not result:
        return {"entities": [], "relationships": []}

    # Validate and normalize entities
    entities = []
    for e in result.get("entities", []):
        name = e.get("name", "").strip()
        etype = e.get("type", "concept").strip().lower()
        desc = e.get("description", "").strip()

        if not name or not _is_valid_entity(name, etype):
            continue

        if etype not in ENTITY_TYPES:
            etype = "concept"

        entities.append({
            "name": name,
            "type": etype,
            "description": desc,
        })

    # Validate relationships
    relationships = []
    for r in result.get("relationships", []):
        source = r.get("source", "").strip()
        pred = r.get("predicate", "").strip().lower()
        target = r.get("target", "").strip()
        if source and target and pred and source != target:
            if pred not in RELATIONSHIP_TYPES:
                pred = _closest_rel_type(pred)
            relationships.append({
                "source": source,
                "predicate": pred,
                "target": target,
            })

    return {"entities": entities, "relationships": relationships}


def _is_valid_entity(name: str, etype: str) -> bool:
    """Check if an entity name passes structural validity checks."""
    name_lower = name.lower()

    # Too short
    if len(name) < 2:
        return False

    # In noise list
    if name_lower in _NOISE_ENTITIES:
        return False

    # Code fragments
    if any(c in name for c in "[](){}\"'`$=;"):
        return False

    # Absolute paths
    if name.startswith("/") or name.startswith("~"):
        return False

    # Dotted module references (server.db, os.path)
    if "." in name and " " not in name and not name[0].isupper():
        return False

    # Underscored identifiers
    if "_" in name and " " not in name:
        return False

    # File extensions
    if re.match(r'.*\.(py|js|ts|json|md|txt|html|css|toml|yaml|yml)$', name_lower):
        return False

    # Purely numeric
    if re.match(r'^[\d.:]+$', name):
        return False

    # Too many words (descriptions, not entity names)
    if len(name.split()) > 5:
        return False

    # Single lowercase word — almost never a real entity
    if len(name.split()) == 1 and name_lower == name and len(name) < 5:
        return False

    return True


def _closest_rel_type(pred: str) -> str:
    """Map free-form predicates to our defined relationship types."""
    pred_lower = pred.lower()
    mappings = {
        "build": "builds", "built": "builds", "building": "builds",
        "use": "uses", "using": "uses", "used": "uses",
        "create": "created", "wrote": "created", "write": "created",
        "made": "created", "develop": "created", "developed": "created",
        "own": "owns", "maintain": "owns", "maintains": "owns",
        "depend": "depends_on", "requires": "depends_on", "need": "depends_on",
        "part": "part_of", "belongs": "part_of", "inside": "part_of",
        "contain": "part_of", "includes": "part_of",
        "work": "works_with", "collaborate": "works_with",
        "configure": "configured_in", "config": "configured_in",
        "set up": "configured_in", "setup": "configured_in",
        "deploy": "deployed_on", "run on": "deployed_on",
        "host": "deployed_on", "serve": "deployed_on",
        "integrat": "works_with", "connect": "works_with",
        "switch": "related_to", "replac": "related_to",
        "install": "uses", "import": "uses",
    }
    for key, val in mappings.items():
        if key in pred_lower:
            return val
    return "related_to"


# ── Main Session Extraction Pipeline ─────────────────────────

def extract_from_session(
    session_text: str,
    session_title: str = "",
    session_header: str = "",
    session_metadata: str | dict = "{}",
    db: MemorableDB | None = None,
    priority: int = 5,
) -> dict:
    """Extract entities and relationships from a session summary.

    This is the primary entry point for KG extraction. Called from:
    - processor.py (when new sessions are processed)
    - bootstrap_kg.py (batch processing existing sessions)

    Args:
        session_text: The session summary (compressed_50 column)
        session_title: Session title for additional context
        session_header: Emoji-tagged header for topic hints
        session_metadata: JSON metadata (may contain GLiNER entities from processing)
        db: Database for storing results and gazetteer lookup
        priority: Base priority for new entities (default 5)

    Returns:
        {"entities_added": int, "relationships_added": int, "entities": list}
    """
    if not session_text or len(session_text.strip()) < 50:
        return {"entities_added": 0, "relationships_added": 0, "entities": []}

    # Build context text from all available sources
    context_parts = []
    if session_title:
        context_parts.append(f"Session: {session_title}")
    if session_header:
        context_parts.append(f"Topics: {session_header}")
    context_parts.append(session_text)

    # Include GLiNER entities from session metadata as hints
    if session_metadata:
        meta = session_metadata if isinstance(session_metadata, dict) else {}
        if isinstance(session_metadata, str):
            try:
                meta = json.loads(session_metadata)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        gliner_ents = meta.get("entities", {})
        if gliner_ents:
            hints = []
            for label, ent_list in gliner_ents.items():
                for e in ent_list[:5]:
                    name = e.get("name", e) if isinstance(e, dict) else str(e)
                    hints.append(f"{name} ({label})")
            if hints:
                context_parts.append(f"Previously detected entities: {', '.join(hints[:15])}")

    full_text = "\n".join(context_parts)

    # --- Phase 1: Haiku extraction (primary) ---
    haiku_result = haiku_extract(full_text)
    all_entities = {e["name"].lower(): e for e in haiku_result["entities"]}
    all_relationships = list(haiku_result["relationships"])

    # --- Phase 2: GLiNER extraction (complementary) ---
    gliner_entities = gliner_extract(session_text)
    for ent in gliner_entities:
        name_lower = ent["name"].lower()
        if name_lower not in all_entities and _is_valid_entity(ent["name"], ent["type"]):
            all_entities[name_lower] = ent

    # --- Phase 3: Gazetteer lookup (recognize known entities) ---
    known_names = set()
    if db:
        try:
            build_gazetteer(db)
            known = gazetteer_lookup(full_text)
            known_names = {e["name"].lower() for e in known}
        except Exception:
            pass

    # --- Phase 4: Store results ---
    entities_list = list(all_entities.values())
    entities_added = 0
    rels_added = 0

    if db and entities_list:
        # Store entities
        for entity in entities_list:
            # Known entities get a small priority boost
            ep = priority + 1 if entity["name"].lower() in known_names else priority
            try:
                db.add_entity(
                    entity["name"],
                    entity["type"],
                    description=entity.get("description", ""),
                    priority=ep,
                )
                entities_added += 1
            except Exception as e:
                print(f"  [kg] Entity store error for '{entity['name']}': {e}")

        # Store relationships (only where both entities exist)
        existing = db.query_kg(min_priority=1, limit=5000)
        existing_names = {e["name"].lower() for e in existing}
        entity_names = {e["name"].lower() for e in entities_list}
        all_known = existing_names | entity_names

        for rel in all_relationships:
            source = rel["source"]
            target = rel["target"]
            if source.lower() in all_known and target.lower() in all_known:
                source_type = _resolve_entity_type(source, entities_list, db)
                target_type = _resolve_entity_type(target, entities_list, db)
                try:
                    db.add_relationship(
                        source_name=source,
                        source_type=source_type,
                        rel_type=rel["predicate"],
                        target_name=target,
                        target_type=target_type,
                    )
                    rels_added += 1
                except Exception:
                    pass

        # Rebuild gazetteer after adding
        if entities_added > 0:
            try:
                build_gazetteer(db)
            except Exception:
                pass

    return {
        "entities_added": entities_added,
        "relationships_added": rels_added,
        "entities": entities_list,
    }


def _resolve_entity_type(name: str, extracted: list[dict],
                          db: MemorableDB) -> str:
    """Resolve the type for an entity name."""
    for e in extracted:
        if e["name"].lower() == name.lower():
            return e["type"]
    results = db.query_kg(entity=name, limit=1)
    if results:
        return results[0].get("type", "concept")
    return "concept"


# ── Legacy Observation Pipeline (kept for compatibility) ─────

def extract_candidates_from_observation(obs_text: str, db: MemorableDB) -> dict:
    """Extract from observation text. Now uses Haiku instead of AFM.

    Kept for backward compatibility with observer.py's KGProcessor.
    """
    known = gazetteer_lookup(obs_text)
    known_names = {e["name"].lower() for e in known}

    # Use GLiNER for quick on-device extraction from observations
    # (don't call Haiku for every individual observation — too expensive)
    gliner_entities = gliner_extract(obs_text, threshold=0.5)
    new_candidates = []
    for ent in gliner_entities:
        if ent["name"].lower() not in known_names and _is_valid_entity(ent["name"], ent["type"]):
            new_candidates.append(ent)
            known_names.add(ent["name"].lower())

    return {
        "entities": new_candidates,
        "relationships": [],
        "known": known,
    }


def sonnet_filter_entities(candidates: list[dict]) -> list[dict]:
    """Use Sonnet to filter entity candidates — keep real, discard garbage.

    With Haiku extraction from session summaries, candidates are much higher
    quality so this filter should approve more entities.
    """
    if not candidates:
        return []

    names_list = "\n".join(
        f'- "{c["name"]}" ({c["type"]})'
        for c in candidates
    )

    prompt = (
        f"I'm building a personal knowledge graph from conversation summaries. "
        f"These entities were extracted by Haiku from session summaries.\n\n"
        f"KEEP entities that are:\n"
        f"- Real people's names\n"
        f"- Named projects or products\n"
        f"- Specific technologies, frameworks, or libraries\n"
        f"- Programming languages\n"
        f"- Real organizations or companies\n"
        f"- Specific hardware or devices\n"
        f"- Important concepts unique to this person's domain\n\n"
        f"REJECT entities that are:\n"
        f"- Generic programming terms (database, server, plugin, etc.)\n"
        f"- Internal code names or file paths\n"
        f"- Vague concepts (system, process, tool)\n\n"
        f"Candidates:\n{names_list}\n\n"
        f"Return JSON: {{\"keep\": [\"Name1\", \"Name2\", ...]}}\n"
        f"If none are worth keeping, return {{\"keep\": []}}"
    )

    result = call_llm_json(
        prompt,
        system="Filter entities for a knowledge graph. Keep specific named things, reject generic words. Return only valid JSON.",
    )
    if not result or "keep" not in result:
        print("  [kg] Sonnet filter failed, rejecting all candidates")
        return []

    keep_names = {n.lower() for n in result["keep"]}
    return [c for c in candidates if c["name"].lower() in keep_names]


def store_approved_entities(
    approved: list[dict],
    relationships: list[dict],
    all_entities: list[dict],
    db: MemorableDB,
) -> dict:
    """Store approved entities and relationships. Kept for compatibility."""
    entities_added = 0
    rels_added = 0
    approved_names = {e["name"].lower() for e in approved}

    for entity in approved:
        db.add_entity(entity["name"], entity["type"], priority=5)
        entities_added += 1

    existing = db.query_kg(min_priority=1, limit=5000)
    existing_names = {e["name"].lower() for e in existing}
    all_known_names = approved_names | existing_names

    for rel in relationships:
        source = rel.get("source", "").strip()
        target = rel.get("target", "").strip()
        pred = rel.get("predicate", "").strip()
        if not (source.lower() in all_known_names and target.lower() in all_known_names):
            continue
        source_type = _resolve_entity_type(source, all_entities, db)
        target_type = _resolve_entity_type(target, all_entities, db)
        try:
            db.add_relationship(
                source_name=source, source_type=source_type,
                rel_type=pred,
                target_name=target, target_type=target_type,
            )
            rels_added += 1
        except Exception:
            pass

    return {"entities_added": entities_added, "relationships_added": rels_added}


# ── Batch Processor Integration ─────────────────────────────

class KGProcessor:
    """Processes observations for KG extraction. Called from the watcher."""

    def __init__(self, config: Config):
        self.config = config
        self.db = MemorableDB(
            Path(config["db_path"]),
            sync_url=config.get("sync_url", ""),
            auth_token=config.get("sync_auth_token", ""),
        )
        self._gazetteer_built = False

    _KG_WORTHY_TOOLS = {"Edit", "Write", "Bash", "WebFetch", "WebSearch"}

    def process_observations(self, observations: list[dict]) -> dict:
        """Extract KG data from a batch of observations.

        Uses GLiNER for real-time observation extraction (fast, on-device).
        Session-level Haiku extraction happens in processor.py instead.
        """
        if not observations:
            return {"entities_added": 0, "relationships_added": 0}

        if not self._gazetteer_built:
            build_gazetteer(self.db)
            self._gazetteer_built = True

        all_candidates = []
        for obs in observations:
            tool = obs.get("tool_name", "")
            if tool not in self._KG_WORTHY_TOOLS:
                continue

            text = obs.get("summary", "")
            if len(text.strip()) < 20:
                continue

            try:
                result = extract_candidates_from_observation(text, self.db)
                all_candidates.extend(result["entities"])
            except Exception as e:
                print(f"  KG extraction error: {e}")

        if not all_candidates:
            return {"entities_added": 0, "relationships_added": 0}

        # Deduplicate
        seen = set()
        unique = []
        for c in all_candidates:
            key = c["name"].lower()
            if key not in seen:
                seen.add(key)
                unique.append(c)

        print(f"  KG: {len(unique)} candidates from {len(observations)} observations")

        # Store directly (GLiNER quality is good enough for priority 4)
        entities_added = 0
        for entity in unique:
            try:
                self.db.add_entity(entity["name"], entity["type"], priority=4)
                entities_added += 1
            except Exception:
                pass

        if entities_added > 0:
            build_gazetteer(self.db)

        return {"entities_added": entities_added, "relationships_added": 0}
