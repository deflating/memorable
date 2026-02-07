# Knowledge Graph Improvement Brief

## The Problem

Memorable's knowledge graph is supposed to grow organically from conversations — capturing people, projects, technologies, decisions, and how they relate. Instead, after processing **1,000+ observations** and **50+ sessions**, the KG has only **11 entities and 3 relationships**. That's a catastrophic failure rate.

The root cause: the extraction pipeline produces mostly garbage, and the filtering is so aggressive that it kills almost everything — including the real entities.

## Current Pipeline (and why it fails)

```
Observation text (from tool calls)
    ↓
Tier 1: NLGazetteer — lookup known entities (only 11 exist, so this finds almost nothing)
    ↓
Tier 2: Apple AFM (~3B model) — extract candidates as JSON
    ↓  ← THIS PRODUCES GARBAGE: SQL fragments, variable names, file paths, generic words
    ↓
Tier 3: Apple NLTagger — catch person/org names AFM missed
    ↓  ← ALSO NOISY: tags "Claude" as PersonalName, common words as organizations
    ↓
Static noise filter: 150+ hardcoded words in _NOISE_ENTITIES
    ↓  ← UNSUSTAINABLE: manually growing a blocklist is a losing game
    ↓
Sonnet filter: one API call per batch to approve/reject candidates
    ↓  ← WORKS but gets mostly garbage input, so rejects almost everything
    ↓
Store approved entities at priority 4
    ↓
Result: almost nothing gets through
```

### Specific Failures

1. **Apple AFM (~3B) is too small for structured extraction.** It outputs things like:
   - `"DESC LIMIT"`, `"SELECT COUNT"` (SQL fragments)
   - `"server.db"`, `"_format_session"` (code identifiers)
   - `"file"`, `"database"`, `"watcher"` (generic words)
   - The noise filter catches most of these, but it's a whack-a-mole game

2. **Source text is wrong.** We extract from raw tool call output (file contents, grep results, command output). This is terrible for entity extraction — it's mostly code, not natural language. We should be extracting from session summaries and conversation text instead.

3. **No relationship intelligence.** Even when entities get through, relationships are weak. AFM extracts `"related_to"` for everything.

4. **The gazetteer feedback loop is dead.** It's supposed to make the system faster by recognizing known entities. But with only 11 entities, it recognizes almost nothing.

5. **No temporal or importance signal.** An entity mentioned once in passing gets the same treatment as one discussed in 20 sessions.

## What We Have to Work With

### On-Device (zero API cost)
- **Apple NLEmbedding** — 512-dim sentence embeddings. Already used for search. Could be used for entity clustering/dedup.
- **Apple NLTagger** — Named entity recognition (PersonalName, OrganizationName, PlaceName). Noisy but catches real names.
- **Apple NLGazetteer** — Custom dictionary lookup. Fast once populated with good data.
- **Apple AFM** — ~3B foundation model via `afm` CLI. Good for classification/labeling, bad for structured extraction.
- **GLiNER** — Zero-shot NER model, already installed (~200MB). Labels: person, technology, software tool, project.
- **YAKE** — Unsupervised keyword extraction. Already used for session metadata.

### API-Based (costs usage)
- **Haiku** via `claude -p` — Used for session summaries. Cheap, fast. Could be used for entity extraction.
- **Sonnet** via `claude -p` — Used for entity filtering. More expensive but very accurate.
- **call_llm()** in `server/llm.py` — Ready-to-use wrapper for both.

### Rich Text Sources Available
- **Session summaries** (`compressed_50` column) — 2-4 paragraph Haiku summaries of each session. Natural language, mentions real projects/people/technologies.
- **Observation summaries** — One-line descriptions of tool usage. Less rich but high volume.
- **User prompts** — What Matt actually said. Rich with intent and context.
- **Session headers** — Emoji-tagged topics like "🔧 Built auth | ✅ Chose JWT". Compact but topical.
- **Session metadata** — YAKE keywords + GLiNER entities already extracted per session.

### Database
- `kg_entities` table with priority system (1-10, 10=sacred)
- `kg_relationships` with typed edges
- Full session + observation + prompt history

## What Good Looks Like

A healthy KG for Memorable would have:
- **People**: Matt, specific collaborators mentioned in conversations
- **Projects**: Memorable, Signal Claude, specific things Matt is building
- **Technologies**: Specific frameworks, tools, languages discussed (React, SQLite, Apple MLX, etc.)
- **Decisions**: Key architectural choices ("chose JWT over sessions", "switched from LLMLingua to Haiku")
- **Concepts**: Important ideas discussed across sessions
- **Relationships**: "Memorable uses SQLite", "Matt builds Memorable", "Signal Claude deployed_on Mac Mini"
- **100-500+ entities** with meaningful relationships, not 11

## Files to Modify

- `plugin/server/kg.py` — The main extraction pipeline. The three-tier system, noise filters, Sonnet filter, gazetteer.
- `plugin/server/observer.py` — Where observations are generated and passed to KG extraction.
- `plugin/server/processor.py` — Session processing pipeline. Could trigger KG extraction from summaries.
- `plugin/server/db.py` — Add queries if needed for new KG data access patterns.
- `plugin/server/llm.py` — LLM interface if you want to use Haiku/Sonnet differently.
- `plugin/server/embeddings.py` — Shared embedding utilities.
- `plugin/cleanup_kg.py` — Existing KG maintenance script (dedup, priority adjustment).
- `plugin/server/config.py` — If you add new configuration options.

## Possible Approaches (not prescriptive — use your judgment)

### 1. Extract from Better Sources
Instead of raw tool output, extract from session summaries (compressed_50), conversation text, and YAKE keywords. These are natural language with real entity mentions.

### 2. Use Haiku for Extraction
Session summaries are already Haiku-generated. A targeted Haiku call to extract structured entities from summaries could be much better than AFM. Something like:
```
"Extract named entities (people, projects, technologies, decisions) from this session summary. Return JSON."
```

### 3. Entity Frequency/Co-occurrence
Track how often entities are mentioned across sessions. An entity mentioned in 10 sessions is probably real. Use mention frequency as a confidence signal instead of (or in addition to) the Sonnet filter.

### 4. Relationship Mining from Context
Instead of asking AFM to extract relationships, infer them from co-occurrence: if "React" and "Memorable" appear in the same session 5 times, they're related.

### 5. Better NER
GLiNER is already installed and works well for specific entity types. It could replace AFM for candidate extraction. Try different label sets or thresholds.

### 6. Embedding-Based Entity Clustering
Use NLEmbedding to group similar entity candidates: "React.js", "React", "ReactJS" → one entity.

### 7. Batch Processing from Session History
Run a one-time extraction over all existing session summaries to bootstrap the KG from the rich data we already have.

### 8. spaCy or Other NLP Libraries
If you want a better NER pipeline, spaCy is a well-tested option. `pip install spacy && python -m spacy download en_core_web_sm`.

## Running & Testing

```bash
# Start the web server to see KG visualization
cd /Users/claude/memorable/plugin && python3 -m server.web --port 7777
# → http://127.0.0.1:7777/#kg

# Process pending observations (triggers KG extraction)
cd /Users/claude/memorable/plugin && python3 -c "
from server.observer import ObservationProcessor
from server.config import Config
proc = ObservationProcessor(Config())
proc.process_queue()
"

# Check KG state
cd /Users/claude/memorable/plugin && python3 -c "
from server.db import MemorableDB
from pathlib import Path
db = MemorableDB(Path.home() / '.memorable' / 'memorable.db')
for e in db.get_all_entities(limit=50):
    print(f'p:{e[\"priority\"]} [{e[\"type\"]:12s}] {e[\"name\"]}')
print(f'---')
stats = db.get_stats()
print(f'{stats[\"kg_entities\"]} entities, {stats[\"kg_relationships\"]} relationships')
"

# Run KG cleanup
cd /Users/claude/memorable/plugin && python3 cleanup_kg.py

# Test Haiku extraction
cd /Users/claude/memorable/plugin && python3 -c "
from server.llm import call_llm_json
result = call_llm_json(
    'Extract named entities from: \"Matt worked on the Memorable plugin, using SQLite and Apple NLEmbedding for the knowledge graph. He switched from LLMLingua to Haiku for session summaries.\" Return: {\"entities\": [{\"name\": \"...\", \"type\": \"person|project|technology|decision\"}]}',
    system='Return only valid JSON. No preamble.',
    model='haiku'
)
print(result)
"
```

## Constraints
- macOS only (Apple ML frameworks)
- Don't break the MCP server or hooks
- Database schema can be extended (add columns, tables) but don't remove existing ones
- API calls to Haiku/Sonnet are fine but be mindful of volume — batch where possible
- The web viewer's KG tab reads from `/api/kg` which returns `{nodes, edges}` from `db.get_kg_graph()`
