"""Knowledge graph extraction pipeline for Memorable.

Uses a three-tier approach, all on-device:
1. NLGazetteer — instant lookup of known entities (grows with KG)
2. NLTagger — catch new person names
3. afm — classify unknown entities and extract relationships

The gazetteer creates a feedback loop: extracted entities become
future recognition targets, so the system gets faster over time.
"""

import json
import re
import subprocess
from pathlib import Path

from .db import MemorableDB
from .config import Config

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

# Short names and common words that NLTagger falsely tags
_NOISE_WORDS = {
    "kg", "db", "ml", "ai", "ui", "api", "url", "cli", "ssh",
    "the", "and", "for", "with", "from", "into", "code", "data",
    "app", "run", "set", "get", "new", "add", "use",
}

# Generic words that afm sometimes extracts as entities.
# Must be lowercase. Anything in here won't become a KG entity.
_NOISE_ENTITIES = {
    # Generic programming terms
    "tool", "file", "code", "database", "function", "method",
    "class", "module", "script", "command", "output", "input",
    "error", "result", "test", "data", "config", "configuration",
    "server", "client", "request", "response", "system", "process",
    "log", "logs", "directory", "path", "string", "number", "list",
    "table", "column", "row", "query", "value", "key", "text",
    "watcher", "pipeline", "daemon", "handler", "processor",
    "framework", "library", "package", "dependency", "import",
    # Generic action words
    "change", "update", "delete", "read", "write", "edit",
    "search", "create", "build", "start", "stop", "check",
    "restarted", "cleared", "printed", "checked", "verified",
    "edited code", "specified text",
    # Memorable internal terms (not useful as entities)
    "entities", "relationships", "observations", "prompts",
    "knowledge graph", "knowledge graphs", "watcher log",
    "kg extraction", "entity validation logic", "kg code",
    "kg growth statistics", "kg processing issues",
    "observation processing pipeline",
    # Entity type names (afm sometimes outputs these as entities)
    "person", "project", "technology", "organization",
    "file", "concept", "tool", "service", "language",
    # Table/column names that leak through
    "kg_entities", "kg_relationships", "sessions",
    "observations_queue", "user_prompts", "processing_queue",
    # Short file basenames (prefer full relative paths)
    "kg.py", "db.py", "web.py", "config.py", "observer.py",
    "watcher.py", "mcp_server.py", "__main__.py",
    # Common words that slip through
    "noise", "filter", "filtering", "extraction",
    "summary", "context", "hook", "hooks",
    "fresh start", "clean", "cleanup",
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

    # Get all entities from DB
    entities = db.query_kg(limit=5000)
    data = {}
    for e in entities:
        etype = e.get("type", "concept")
        name = e.get("name", "")
        if name:
            data.setdefault(etype, []).append(name)

    # Only rebuild if data changed
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
        # PyObjC may return (gaz, err) tuple or just gaz
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
    """Find known entities in text using the cached gazetteer.

    Returns list of {name, type} for entities found.
    """
    if _gazetteer is None or not _gazetteer_data:
        return []

    found = []
    # Check each known entity name against the text
    for etype, names in _gazetteer_data.items():
        for name in names:
            if name.lower() in text.lower():
                label = _gazetteer.labelForString_(name)
                if label:
                    found.append({"name": name, "type": label})
    return found


# ── NLTagger — Person Name Detection ───────────────────────

def extract_named_entities(text: str) -> list[dict]:
    """Use NLTagger NameType to find named entities in text.

    Returns list of {name, type} where type is 'person' or 'organization'.
    """
    try:
        ns = _load_nl()
        NLTagger = ns['NLTagger']
        NLTokenizer = ns['NLTokenizer']
        from Foundation import NSMakeRange

        clipped = text[:1000]

        # Tokenize first
        tokenizer = NLTokenizer.alloc().initWithUnit_(0)  # 0 = word
        tokenizer.setString_(clipped)
        tokens = tokenizer.tokensForRange_(NSMakeRange(0, len(clipped)))

        # Tag each token
        tagger = NLTagger.alloc().initWithTagSchemes_(['NameType'])
        tagger.setString_(clipped)

        entities = []
        prev_tag = None
        prev_word = None

        for tr in tokens:
            loc = tr.rangeValue().location
            length = tr.rangeValue().length
            word = clipped[loc:loc + length]
            tag = str(tagger.tagAtIndex_unit_scheme_tokenRange_(
                loc, 0, 'NameType', None
            ) or '')

            if tag == 'PersonalName':
                # Merge consecutive PersonalName tokens ("Claude" "Code")
                if prev_tag == 'PersonalName' and prev_word:
                    entities[-1]["name"] += f" {word}"
                else:
                    entities.append({"name": word, "type": "person"})
                prev_tag = tag
                prev_word = word
            elif tag == 'OrganizationName':
                if prev_tag == 'OrganizationName' and prev_word:
                    entities[-1]["name"] += f" {word}"
                else:
                    entities.append({"name": word, "type": "organization"})
                prev_tag = tag
                prev_word = word
            elif tag == 'PlaceName':
                entities.append({"name": word, "type": "concept"})
                prev_tag = tag
                prev_word = word
            else:
                prev_tag = None
                prev_word = None

        # Deduplicate preserving order
        seen = set()
        unique = []
        for e in entities:
            key = e["name"].lower()
            if key not in seen and len(e["name"]) > 1:
                seen.add(key)
                unique.append(e)
        return unique
    except Exception as e:
        print(f"  NLTagger error: {e}")
        return []


# ── AFM — Entity & Relationship Extraction ──────────────────

_KG_PROMPT = """\
Extract NAMED entities and relationships from this text. Only include proper nouns, specific project names, specific technology names, and real people. Do NOT include generic words like "tool", "file", "code", "database", "watcher", "observations". Return ONLY valid JSON, no markdown fences.

Entity types: person, project, technology, organization, file, concept, tool, service, language
Relationship types: uses, builds, created, owns, depends_on, part_of, works_with, configured_in, deployed_on, related_to

Text: {text}

Return: {{"entities": [{{"name": "...", "type": "..."}}], "relationships": [{{"source": "...", "predicate": "...", "target": "..."}}]}}\
"""


def _call_afm(prompt: str) -> str:
    """Call Apple Foundation Model via afm CLI."""
    env = {**__import__("os").environ, "TOKENIZERS_PARALLELISM": "false"}
    try:
        result = subprocess.run(
            ["afm", "-s", prompt],
            capture_output=True, text=True, timeout=30, env=env,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _parse_afm_json(raw: str) -> dict | None:
    """Parse JSON from afm output, stripping markdown fences if present."""
    text = raw.strip()
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def afm_extract(text: str) -> dict:
    """Use afm to extract entities and relationships from text.

    Returns {"entities": [...], "relationships": [...]}
    """
    prompt = _KG_PROMPT.format(text=text[:800])
    raw = _call_afm(prompt)
    if not raw:
        return {"entities": [], "relationships": []}

    result = _parse_afm_json(raw)
    if not result:
        return {"entities": [], "relationships": []}

    # Validate and normalize
    entities = []
    for e in result.get("entities", []):
        name = e.get("name", "").strip()
        etype = e.get("type", "concept").strip().lower()
        if not name or len(name) < 3 or etype not in ENTITY_TYPES:
            continue
        if name.lower() in _NOISE_ENTITIES or name.lower() in _NOISE_WORDS:
            continue
        # Skip absolute paths, home dirs, and usernames
        if name.startswith("/") or name.startswith("~"):
            continue
        if name.lower().startswith("mattkennelly"):
            continue
        # Skip phrases with too many words (likely descriptions, not entities)
        if len(name.split()) > 4:
            continue
        entities.append({"name": name, "type": etype})

    relationships = []
    for r in result.get("relationships", []):
        source = r.get("source", "").strip()
        pred = r.get("predicate", "").strip().lower()
        target = r.get("target", "").strip()
        if source and target and pred:
            # Normalize predicate to closest known type
            if pred not in RELATIONSHIP_TYPES:
                pred = _closest_rel_type(pred)
            relationships.append({
                "source": source, "predicate": pred, "target": target,
            })

    return {"entities": entities, "relationships": relationships}


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
    }
    for key, val in mappings.items():
        if key in pred_lower:
            return val
    return "related_to"


# ── Main Extraction Pipeline ────────────────────────────────

def extract_kg_from_observation(obs_text: str, db: MemorableDB,
                                 session_id: str = "") -> dict:
    """Run the full KG extraction pipeline on an observation.

    1. Gazetteer lookup (instant — known entities)
    2. NLTagger (instant — person names)
    3. afm (slow — unknowns, relationships)
    4. Store everything in the KG

    Returns {"entities_added": int, "relationships_added": int}
    """
    entities_added = 0
    rels_added = 0

    # --- Tier 1: Gazetteer (known entities) ---
    known = gazetteer_lookup(obs_text)
    known_names = {e["name"].lower() for e in known}

    # --- Tier 2: afm (authoritative entity types + relationships) ---
    afm_result = afm_extract(obs_text)

    for entity in afm_result["entities"]:
        if entity["name"].lower() not in known_names:
            db.add_entity(
                entity["name"], entity["type"],
                priority=4,  # auto-extracted
            )
            entities_added += 1
            known_names.add(entity["name"].lower())

    # --- Tier 3: NLTagger (catch person/org names afm missed) ---
    tagger_entities = extract_named_entities(obs_text)
    for ent in tagger_entities:
        name = ent["name"]
        if len(name) < 3 or name.lower() in _NOISE_WORDS:
            continue
        if name.lower() in _NOISE_ENTITIES:
            continue
        # Skip things that look like unix usernames or paths
        if name.lower().startswith("mattkennelly") or "/" in name:
            continue
        if name.lower() not in known_names:
            db.add_entity(name, ent["type"], priority=5)
            entities_added += 1
            known_names.add(name.lower())

    for rel in afm_result["relationships"]:
        # We need to figure out the types for source and target
        source_type = _resolve_entity_type(rel["source"], afm_result["entities"], db)
        target_type = _resolve_entity_type(rel["target"], afm_result["entities"], db)
        try:
            db.add_relationship(
                source_name=rel["source"],
                source_type=source_type,
                rel_type=rel["predicate"],
                target_name=rel["target"],
                target_type=target_type,
            )
            rels_added += 1
        except Exception:
            pass  # skip duplicate or broken relationships

    return {"entities_added": entities_added, "relationships_added": rels_added}


def _resolve_entity_type(name: str, afm_entities: list[dict],
                          db: MemorableDB) -> str:
    """Resolve the type for an entity name — check afm results, then DB, then default."""
    # Check afm extraction results first
    for e in afm_entities:
        if e["name"].lower() == name.lower():
            return e["type"]
    # Check DB
    results = db.query_kg(entity=name, limit=1)
    if results:
        return results[0].get("type", "concept")
    return "concept"


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

    # Tools whose output is interesting for KG extraction
    _KG_WORTHY_TOOLS = {"Edit", "Write", "Bash", "WebFetch", "WebSearch"}

    def process_observations(self, observations: list[dict]) -> dict:
        """Extract KG data from a batch of observations.

        Only processes substantive observations (edits, writes, commands).
        Uses tool input/output for richer extraction context.
        """
        if not observations:
            return {"entities_added": 0, "relationships_added": 0}

        # Rebuild gazetteer if needed (once per batch)
        if not self._gazetteer_built:
            build_gazetteer(self.db)
            self._gazetteer_built = True

        total_entities = 0
        total_rels = 0

        for obs in observations:
            # Only extract KG from substantive tool operations
            tool = obs.get("tool_name", "")
            if tool not in self._KG_WORTHY_TOOLS:
                continue

            # Build extraction text from tool data + observation summary
            parts = [obs.get("summary", "")]
            tool_input = obs.get("tool_input", "")
            if tool_input:
                parts.append(f"Tool input: {tool_input[:300]}")

            text = "\n".join(parts)
            if len(text.strip()) < 20:
                continue

            try:
                result = extract_kg_from_observation(
                    text, self.db,
                    session_id=obs.get("session_id", ""),
                )
                total_entities += result["entities_added"]
                total_rels += result["relationships_added"]
            except Exception as e:
                print(f"  KG extraction error: {e}")

        # Rebuild gazetteer after adding new entities
        if total_entities > 0:
            build_gazetteer(self.db)

        return {
            "entities_added": total_entities,
            "relationships_added": total_rels,
        }
