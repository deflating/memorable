# Memorable — Architecture

Persistent memory system for Claude Code. Automatically captures session transcripts, generates summaries, extracts a knowledge graph, and makes everything searchable — all running on-device with Apple ML frameworks.

## How It Fits Together

```
Claude Code Session
    │
    ├─ SessionStart hook ──→ Load startup seed (sacred facts, recent sessions)
    │
    ├─ UserPromptSubmit hook ──→ Capture + embed user messages (Apple NLEmbedding)
    │
    ├─ PostToolUse hook ──→ Queue tool calls for async processing
    │     │
    │     └─ [async every 30s] ObservationProcessor
    │           ├─ Generate deterministic observation (no LLM)
    │           ├─ Embed with Apple NLEmbedding (512-dim)
    │           └─ KG extraction: afm candidates → Sonnet filter → store
    │
    ├─ PreCompact hook ──→ Remind to save important context
    │
    └─ Stop hook ──→ Generate session-level summary from observations
         │
         └─ [async, when transcript idle 15m] TranscriptProcessor
               ├─ LLMLingua-2 compression at 50%
               ├─ Apple AFM: emoji header tags
               ├─ Apple AFM: session note (~100 words)
               └─ Store in sessions table
```

## Directory Layout

```
memorable/
├── plugin/
│   ├── .mcp.json                    # MCP server config → spawns `python3 -m server --watch`
│   ├── .claude-plugin/
│   │   └── plugin.json              # Plugin metadata (name, version, description)
│   ├── pyproject.toml               # Dependencies: llmlingua, watchdog, libsql-experimental
│   │
│   ├── server/                      # Core application
│   │   ├── __main__.py              # CLI: 5 modes (mcp, watch, watcher, process, init)
│   │   ├── config.py                # Loads ~/.memorable/config.json
│   │   ├── db.py                    # SQLite/libSQL database layer
│   │   ├── mcp_server.py            # MCP JSON-RPC server with 7 tools
│   │   ├── processor.py             # Transcript → compressed → summary pipeline
│   │   ├── observer.py              # Tool calls → observations + embeddings
│   │   ├── kg.py                    # Knowledge graph extraction (afm + NLTagger + Sonnet)
│   │   ├── llm.py                   # Claude CLI wrapper (Sonnet via `claude -p`)
│   │   ├── web.py                   # HTTP server for web viewer
│   │   ├── watcher.py               # File watcher + background processing loop
│   │   └── import_sessions.py       # Legacy session note importer
│   │
│   ├── hooks/
│   │   ├── hooks.json               # 5 lifecycle hook definitions
│   │   └── scripts/
│   │       ├── session_start.py     # → stdout injected as context
│   │       ├── user_prompt.py       # → capture + embed
│   │       ├── post_tool_use.py     # → queue for async processing
│   │       ├── session_stop.py      # → generate session summary
│   │       └── pre_compact.py       # → advisory reminder
│   │
│   ├── ui/
│   │   └── viewer.html              # Web UI (dark theme, tabs, KG graph)
│   │
│   ├── skills/                      # Claude Code skill definitions
│   └── commands/                    # Command metadata
│
└── reprocess.py                     # Batch reprocessing script
```

## Database Schema

**Location:** `~/.memorable/memorable.db`

### sessions
Processed session records with compressed transcripts and LLM-generated summaries.

| Column | Type | Description |
|--------|------|-------------|
| transcript_id | TEXT UNIQUE | JSONL filename stem |
| date | TEXT | Session date (YYYY-MM-DD) |
| title | TEXT | Extracted from first substantive user message |
| summary | TEXT | AFM-generated ~100-word session note |
| header | TEXT | Emoji tags: `🔧 Built auth \| ✅ Chose JWT` |
| compressed_50 | TEXT | LLMLingua-2 at 50% — searchable archive |
| source_path | TEXT | Original JSONL path |
| message_count | INTEGER | Total messages in session |
| word_count | INTEGER | Total words |
| human_word_count | INTEGER | Words from user only |

### observations
Tool usage events with deterministic descriptions and 512-dim embeddings.

| Column | Type | Description |
|--------|------|-------------|
| session_id | TEXT | Claude Code session ID |
| observation_type | TEXT | discovery, change, bugfix, feature, refactor, decision, session_summary |
| title | TEXT | e.g. "Edited server/kg.py" |
| summary | TEXT | Deterministic description from tool metadata |
| files | TEXT | JSON array of file paths touched |
| embedding | BLOB | float32 512-dim from Apple NLEmbedding |
| tool_name | TEXT | Read, Edit, Write, Bash, Grep, etc. |

### observations_queue
Pending tool calls waiting for async processing.

| Column | Type | Description |
|--------|------|-------------|
| session_id | TEXT | Session that generated this |
| tool_name | TEXT | Which tool was called |
| tool_input | TEXT | Tool arguments (truncated) |
| tool_response | TEXT | Tool output (truncated to 3000 chars) |
| context_before | TEXT | Last assistant message (~500 chars) |
| context_after | TEXT | Last user message (~500 chars) |
| status | TEXT | pending → processed \| skipped |

### kg_entities
Knowledge graph nodes.

| Column | Type | Description |
|--------|------|-------------|
| name | TEXT | Entity name (e.g. "React", "Matt Kennelly") |
| type | TEXT | person, project, technology, organization, file, concept, tool, service, language |
| priority | INTEGER | 10=sacred, 7-9=important, 4-6=contextual, 1-3=ephemeral |
| description | TEXT | Optional description |
| metadata | TEXT | JSON blob |

UNIQUE constraint on (name, type).

### kg_relationships
Knowledge graph edges.

| Column | Type | Description |
|--------|------|-------------|
| source_id | INTEGER | FK → kg_entities |
| target_id | INTEGER | FK → kg_entities |
| rel_type | TEXT | uses, builds, created, owns, depends_on, part_of, works_with, configured_in, deployed_on, related_to |
| confidence | REAL | Default 1.0 |

### user_prompts
Captured user messages with embeddings for semantic search.

| Column | Type | Description |
|--------|------|-------------|
| session_id | TEXT | Session ID |
| prompt_number | INTEGER | Sequence within session |
| prompt_text | TEXT | User's message (system-reminder blocks stripped) |
| embedding | BLOB | float32 512-dim |

### processing_queue
Transcript processing queue (deduplication via file hash).

| Column | Type | Description |
|--------|------|-------------|
| transcript_path | TEXT | Full path to JSONL |
| file_hash | TEXT UNIQUE | MD5[:12] for dedup |
| status | TEXT | pending → done \| error |
| error | TEXT | Error message if failed |

## MCP Tools

The MCP server exposes 7 tools to Claude Code:

### memorable_get_startup_seed
Returns sacred facts (priority 10), important entries (7-9), and recent session summaries. Called automatically by the SessionStart hook.

### memorable_search_sessions
Keyword search over session titles, compressed transcripts, and summaries. Returns matching sessions with title, date, header, and preview.

### memorable_search_observations
Hybrid semantic + keyword search. Embeds the query via Apple NLEmbedding, computes cosine distance against stored embeddings (threshold < 0.6), combines with keyword matches. Score = 0.7 × (1 - distance) + 0.3. Searches both observations and user prompts.

### memorable_get_observations
List observations for a specific session or recent across all sessions.

### memorable_record_significant
Manually save important moments to the KG as entities. Accepts description, optional entity name, type, and priority (1-10). Priority 10 = sacred (immutable on update).

### memorable_query_kg
Query the knowledge graph by entity name, type, or minimum priority. Returns entities with their relationships formatted as `entity → [rel_type] → target`.

### memorable_get_system_status
System health: session count, KG entity count, pending queue, total words processed, config summary.

## Pipelines

### Transcript Processing
**File:** `server/processor.py`

```
JSONL transcript (idle 15+ min)
    ↓
Validate: ≥15 messages, ≥100 human words
    ↓
Skip: autonomous wakeup sessions (< 5% human words)
    ↓
Format conversation as "Matt: ... / Claude: ..." text
    ↓
LLMLingua-2 compress at 50% (force-preserve: \n ** : . ? !)
    ↓
Apple AFM → emoji header (first 800 words of compressed)
    ↓
Apple AFM → session note (first/last 1800 words of raw)
    ↓
Extract title from first substantive human message
    ↓
Store in sessions table
```

**Model:** LLMLingua uses `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank` (~500MB, loaded once, runs on CPU).

**Session notes** are generated by Apple's on-device Foundation Model (~3B params). Quality is limited — this is the first target for replacement by an MLX fine-tuned model.

### Observation Generation
**File:** `server/observer.py`

No LLM involved. Observations are built deterministically from tool metadata:

| Tool | Example Output |
|------|---------------|
| Read | "Read server/kg.py (lines 100-200)" |
| Edit | "Replaced 'old_code' with 'new_code' in kg.py" |
| Write | "Wrote server/llm.py (90 lines)" |
| Bash | Uses Claude's `description` field, or extracts primary command |
| Grep | "Searched for 'pattern' (42 matches in server/)" |
| Glob | "Found files matching '*.py' (8 files)" |
| WebFetch | "Fetched example.com" |
| WebSearch | "Searched web for 'query'" |

Each observation is embedded via Apple NLEmbedding (512-dim float32) and stored with its session ID, files touched, and observation type.

### Knowledge Graph Extraction
**File:** `server/kg.py`

Three-tier extraction followed by a Sonnet quality filter:

```
Observation text
    ↓
Tier 1: NLGazetteer — instant lookup of known entities (feedback loop)
    ↓
Tier 2: Apple AFM — extract candidate entities + relationships as JSON
    ↓
Tier 3: Apple NLTagger — catch person/org names AFM missed
    ↓
Batch all candidates across observations, deduplicate
    ↓
ONE Sonnet call via claude CLI: filter real entities from garbage
    ↓
Store approved entities (priority 4) + relationships
    ↓
Rebuild gazetteer (new entities become future lookup targets)
```

**Why Sonnet for filtering:** AFM (~3B) can't reliably distinguish real named entities from code artifacts. SQL fragments, variable names, CLI commands, and file paths all slip through. The blocklist approach was unsustainable. Sonnet gets one tiny call per batch (~20 entity names, ~7 seconds) and perfectly separates real from garbage.

**Why not Sonnet for everything:** Usage limits. The KG filter is a tiny prompt (a few hundred tokens). Session notes would require sending full transcripts — too expensive per session.

### Search
**File:** `server/mcp_server.py` (memorable_search_observations)

Hybrid approach combining keyword and semantic search:

1. **Keyword pass:** SQL LIKE queries on observation title/summary and prompt text
2. **Semantic pass:** Embed query via Apple NLEmbedding, compute cosine distance against all stored embeddings, threshold at distance < 0.6
3. **Scoring:** `score = 0.7 × (1 - cosine_distance) + 0.3 × keyword_boost`
4. **Merge:** Deduplicate, rank by score, return top N

Searches both observations and user_prompts in a single call.

## CLI Modes

**Entry point:** `python3 -m server [flags]`

| Flag | Mode | Description |
|------|------|-------------|
| *(none)* | MCP server | JSON-RPC over stdio, no watcher |
| `--watch` | MCP + watcher | Normal operation: MCP server + background file watcher |
| `--watcher` | Watcher only | Standalone daemon (for launchd), no MCP |
| `--process` | Process once | Scan, queue, process all pending transcripts, then exit |
| `--init` | Initialize | Create config + database, then exit |

Production: `--watch` is the default, spawned by Claude Code via `.mcp.json`.

## Web Viewer

**File:** `server/web.py` + `ui/viewer.html`

```bash
python3 -m server.web --port 7777
```

### API Endpoints

| Route | Params | Returns |
|-------|--------|---------|
| `/` | — | HTML UI |
| `/api/stats` | — | Session count, KG stats, words processed |
| `/api/sessions` | `limit`, `q` | Recent or filtered sessions |
| `/api/session` | `id` | Session detail + observations + prompts |
| `/api/timeline` | `limit` | Mixed observations + prompts, chronological |
| `/api/observations` | `limit`, `session_id` | Observation list |
| `/api/prompts` | `limit`, `session_id`, `q` | User messages |
| `/api/search` | `q`, `limit` | Unified search (observations + prompts) |
| `/api/kg` | `min_priority` | Graph data: nodes + edges |

### UI
- Dark theme, gold accent (#f0c000)
- Four tabs: Sessions, Observations, Knowledge Graph, Timeline
- Force-directed canvas graph for KG visualization (pan/zoom/drag/hover)
- Frosted glass header, skeleton loading states, staggered card animations
- Cmd+K search focus, responsive at 768px

## Configuration

**File:** `~/.memorable/config.json`

```json
{
  "memory_dir": "~/claude-memory",
  "db_path": "~/.memorable/memorable.db",
  "transcript_dirs": ["~/.claude/projects"],

  "sync_url": "",
  "sync_auth_token": "",

  "compression_rate_storage": 0.50,

  "watcher_enabled": true,
  "stale_minutes": 15,
  "min_messages": 15,
  "min_human_words": 100,

  "seed_session_count": 10,
  "live_capture_interval": 20,

  "observer_enabled": true,
  "observer_max_tool_output": 3000,
  "observer_process_interval": 30
}
```

## LLM Interface

**File:** `server/llm.py`

Thin wrapper around the `claude` CLI. Uses Claude Code's existing subscription — no separate API key.

```python
call_llm(prompt, system="...", max_tokens=1024) → str
call_llm_json(prompt, system="...") → dict | None
```

Runs `claude -p --model sonnet --system-prompt "..." --no-session-persistence` from `/tmp` (to avoid picking up CLAUDE.md). Timeout: 180 seconds.

Currently used only for KG entity filtering. Designed as a single swap point — when an MLX fine-tuned model is ready, change this one file.

## Dependencies

### Python (pyproject.toml)
- `llmlingua>=0.2` — Prompt compression (MeetingBank BERT model, ~500MB)
- `watchdog>=3.0` — Cross-platform file system monitoring
- `libsql-experimental>=0.0.50` — SQLite with embedded replica support

### System
- macOS (required for Apple ML frameworks)
- Python 3.10+
- `afm` CLI — Apple Foundation Model
- `claude` CLI — For Sonnet entity filtering
- PyObjC — Bridge to NaturalLanguage.framework (NLEmbedding, NLTagger, NLGazetteer)

## Technology Stack

| Component | Technology | On-Device? |
|-----------|-----------|-----------|
| Compression | LLMLingua-2 (BERT) | Yes |
| Session summaries | Apple AFM (~3B) | Yes |
| Observations | Rule-based extraction | Yes |
| Embeddings | Apple NLEmbedding (512-dim) | Yes |
| Named entities | Apple NLTagger (NameType) | Yes |
| Entity candidates | Apple AFM | Yes |
| Entity gazetteer | Apple NLGazetteer | Yes |
| Entity filtering | Claude Sonnet (via CLI) | API call |
| Database | SQLite / libSQL | Yes |
| Web UI | Vanilla HTML/CSS/JS | Yes |
| File watching | watchdog | Yes |

Everything runs locally except the Sonnet entity filter, which makes one small API call per observation batch.

## Future: MLX Fine-Tuned Model

Session notes are the first replacement target. The plan:

1. Collect training data from existing DeepSeek-quality session notes
2. Fine-tune Qwen3-4B (or similar) via `mlx-lm` LoRA on Apple Silicon
3. Fuse adapters into standalone model
4. Swap into `llm.py` — replaces Sonnet for KG filtering too
5. Fully on-device, zero API cost, better quality than AFM

The M4 Mac Mini (24GB) can run a 4B model at ~40-70 tok/s in 4-bit quantization. Fine-tuning with QLoRA needs ~3-4GB.
