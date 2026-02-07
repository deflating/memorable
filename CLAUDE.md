# Memorable — Persistent Memory for Claude Code

## What We're Building

Memorable gives Claude Code **persistent memory across sessions**. Every conversation is captured, summarized, and indexed. A knowledge graph grows over time. When a new session starts, Claude automatically gets context about recent work, key decisions, and important facts — bridging the gap between sessions.

The user is Matt. He has SDAM (Severely Deficient Autobiographical Memory) — he literally can't replay past experiences. External memory systems aren't just useful for him, they're essential. Memorable is that system for his AI interactions.

**The vision:** Claude should never ask "what were we working on?" It should already know. Memory should feel natural — not like querying a database, but like having continuity.

## Architecture Overview

Memorable is a **Claude Code plugin** with lifecycle hooks, an MCP server, a background processor, and a web dashboard.

```
Claude Code Session
    │
    ├─ SessionStart hook → Inject startup context (sacred facts, recent sessions, rolling summary)
    │
    ├─ UserPromptSubmit hook → Capture user messages + embed (Apple NLEmbedding 512-dim)
    │
    ├─ PostToolUse hook → Queue tool calls for async observation processing
    │     └─ Background watcher (every 30s): ObservationProcessor
    │           ├─ Deterministic observation generation (no LLM needed)
    │           ├─ Apple NLEmbedding sentence embeddings
    │           └─ KG extraction: afm candidates → Sonnet filter → store
    │
    ├─ PreCompact hook → Remind Claude to save important context before compaction
    │
    └─ Stop hook → Generate session-level summary from observations
         └─ Background watcher (when transcript idle 15m): TranscriptProcessor
               ├─ Haiku summarization via `claude -p`
               ├─ Apple AFM emoji header tags
               ├─ YAKE keywords + GLiNER entity extraction
               └─ Store in sessions table
```

## Directory Layout

```
memorable/
├── CLAUDE.md                           # This file
├── ARCHITECTURE.md                     # Detailed architecture reference
├── plugin/
│   ├── .claude-plugin/plugin.json      # Plugin metadata
│   ├── .mcp.json                       # MCP server config → spawns `python3 -m server --watch`
│   │
│   ├── server/                         # Core Python backend
│   │   ├── __main__.py                 # CLI entry: mcp, watch, watcher, process, init modes
│   │   ├── config.py                   # ~/.memorable/config.json with defaults
│   │   ├── db.py                       # SQLite/libSQL database layer (all queries)
│   │   ├── mcp_server.py              # MCP JSON-RPC server — 7 tools exposed to Claude
│   │   ├── observer.py                # Tool calls → observations + embeddings (Apple native)
│   │   ├── processor.py               # Transcript → summary pipeline (Haiku + YAKE + GLiNER)
│   │   ├── kg.py                      # Knowledge graph extraction (afm + NLTagger + Sonnet)
│   │   ├── llm.py                     # Claude CLI wrapper (`claude -p`)
│   │   ├── summaries.py              # Rolling 5-day summaries via Haiku
│   │   ├── watcher.py                # File watcher + background processing loop
│   │   ├── web.py                     # HTTP server for web viewer (port 7777)
│   │   └── import_sessions.py        # Legacy session note importer
│   │
│   ├── hooks/
│   │   ├── hooks.json                 # 5 lifecycle hooks: SessionStart, UserPromptSubmit, PostToolUse, PreCompact, Stop
│   │   └── scripts/
│   │       ├── session_start.py       # Output injected as Claude's startup context
│   │       ├── user_prompt.py         # Capture + embed user messages
│   │       ├── post_tool_use.py       # Queue tool calls for async processing
│   │       ├── session_stop.py        # Generate session summary observation
│   │       └── pre_compact.py         # Advisory: remind to save important context
│   │
│   ├── ui/                            # Web viewer (vanilla HTML/CSS/JS, no build tools)
│   │   ├── CLAUDE.md                  # UI-specific docs
│   │   ├── viewer.html                # HTML template
│   │   ├── viewer.css                 # All styles
│   │   └── js/
│   │       ├── app.js                 # Entry point: state, routing, events
│   │       ├── utils.js               # Shared helpers
│   │       ├── components.js          # Card renderers
│   │       ├── timeline.js            # Timeline tab with filter chips
│   │       ├── sessions.js            # Session detail view
│   │       ├── search.js              # Search results
│   │       └── kg.js                  # Knowledge graph canvas visualization
│   │
│   ├── skills/                        # Claude Code skill definitions
│   ├── commands/                      # Command metadata
│   ├── cleanup_kg.py                  # KG maintenance script
│   ├── reprocess.py                   # Batch reprocessing
│   └── reprocess_sessions.py          # Session metadata re-extraction
```

## Database Schema (SQLite at ~/.memorable/memorable.db)

**sessions** — Processed session records with summaries
- `transcript_id` TEXT UNIQUE, `date`, `title`, `summary` (keywords), `header` (emoji tags), `compressed_50` (Haiku summary), `metadata` (JSON: keywords, entities), `message_count`, `word_count`, `human_word_count`

**observations** — Tool usage events with embeddings
- `session_id`, `observation_type` (discovery/change/bugfix/feature/refactor/decision/session_summary), `title`, `summary`, `files` (JSON array), `embedding` (512-dim float32 BLOB), `tool_name`

**observations_queue** — Pending tool calls waiting for processing
- `session_id`, `tool_name`, `tool_input`, `tool_response`, `context_before`, `context_after`, `status`

**user_prompts** — Captured user messages with embeddings
- `session_id`, `prompt_number`, `prompt_text`, `embedding` (512-dim BLOB)

**kg_entities** — Knowledge graph nodes
- `name`, `type` (person/project/technology/organization/file/concept/tool/service/language), `priority` (1-10, 10=sacred/immutable), `description`, `metadata`

**kg_relationships** — Knowledge graph edges
- `source_id` → `target_id`, `rel_type` (uses/builds/created/owns/depends_on/part_of/works_with/configured_in/deployed_on/related_to), `confidence`

**rolling_summaries** — 5-day rolling summaries
**context_seeds** — Conversation checkpoints
**processing_queue** — Transcript processing queue with dedup

## MCP Tools (exposed to Claude Code)

1. **memorable_get_startup_seed** — Sacred facts + recent sessions + important entries
2. **memorable_search_sessions** — Hybrid keyword + semantic search over sessions
3. **memorable_search_observations** — Hybrid search across observations AND user prompts
4. **memorable_get_observations** — Get observations for a session or recent across all
5. **memorable_record_significant** — Manually save important moments to KG (priority 1-10)
6. **memorable_query_kg** — Query knowledge graph by entity/type/priority
7. **memorable_get_system_status** — System health and stats

## Technology Stack

| Component | Technology | On-Device? |
|-----------|-----------|-----------|
| Session summaries | Haiku via `claude -p` | API call (cheap) |
| Emoji headers | Apple AFM (~3B) via `afm` CLI | Yes |
| Observations | Deterministic from tool metadata | Yes |
| Embeddings | Apple NLEmbedding (512-dim) | Yes |
| Keywords | YAKE (unsupervised, ~10MB) | Yes |
| Named entities | GLiNER zero-shot NER (~200MB) | Yes |
| Entity candidates | Apple AFM | Yes |
| Entity filtering | Sonnet via `claude -p` (one call per batch) | API call |
| Entity gazetteer | Apple NLGazetteer (feedback loop) | Yes |
| Database | SQLite / libSQL embedded replica | Yes |
| Web UI | Vanilla HTML/CSS/JS | Yes |
| File watching | watchdog | Yes |

## How Search Works (Hybrid: Keyword + Semantic)

Both session search and observation search use the same pattern:
1. **Keyword pass**: SQL LIKE on title/summary/text fields
2. **Semantic pass**: Apple NLEmbedding cosine distance on ALL items, threshold > 0.15 similarity
3. **Scoring**: Keyword matches get `0.6 * semantic_score + 0.4` (keyword bonus). Semantic-only matches use raw semantic score.
4. **NLEmbedding distances** range 0.8 (very similar) to 1.5 (unrelated). Normalized: `max(0.0, (1.5 - dist) / 0.7)`

## Web Viewer API (port 7777)

| Route | Returns |
|-------|---------|
| `GET /` | HTML viewer |
| `GET /api/stats` | `{sessions, observations, user_prompts, total_words_processed, kg_entities, kg_relationships}` |
| `GET /api/sessions?limit=N` | Recent sessions |
| `GET /api/session?id=TRANSCRIPT_ID` | Session + observations + prompts |
| `GET /api/timeline?limit=N` | Mixed observations + prompts chronologically |
| `GET /api/search?q=QUERY&limit=N` | Keyword search (observations + prompts) |
| `GET /api/kg?min_priority=N` | KG nodes + edges for graph |
| `GET /api/observations?limit=N&session_id=ID` | Observations list |
| `GET /api/prompts?limit=N&session_id=ID&q=QUERY` | User prompts |

## Current Stats (approximate)

- ~50+ sessions stored
- ~1,000+ observations
- ~500+ user prompts captured
- ~200+ KG entities
- ~100+ KG relationships
- Running on Mac Mini M4 (24GB)

## Known Limitations & Improvement Areas

### Startup Seed
- The session_start hook outputs raw text — it could be more structured
- No intelligence about WHAT context is most relevant to the current session
- Sacred facts are just KG entities with priority=10 — could be richer

### Search
- Web viewer search is keyword-only (SQL LIKE) — the MCP tools have hybrid semantic search but the web API doesn't use it
- No search over session summaries/compressed text in the web viewer
- No fuzzy matching or typo tolerance

### Observations
- Context classification (bugfix/feature/refactor) uses simple keyword matching — could be smarter
- No deduplication of similar observations within a session
- Observation summaries are mechanical — they describe WHAT happened but not WHY

### Knowledge Graph
- Entity quality depends heavily on the noise filter list in kg.py (150+ entries)
- No relationship deduplication or merging
- No temporal aspect — when was an entity last relevant?
- No entity importance decay — old entities stay at the same priority forever
- The gazetteer feedback loop can amplify false positives if bad entities get through

### Web Viewer
- Recently improved by Agent Team (animations, filter chips, KG graph features)
- No data export/import
- No ability to edit/delete observations or entities from the UI
- No dark/light theme toggle (dark only)
- No pagination — loads everything at once

### Session Processing
- Haiku summaries are good but could include more structured metadata
- No cross-session analysis (e.g., "these 5 sessions were all part of the same project")
- No automatic project detection or grouping

### Missing Features
- No notification system (e.g., "you haven't worked on X in a while")
- No session comparison or diff
- No automatic "what I learned" extraction
- No integration with external tools (Notion, calendar, etc.)
- No voice/audio input support in the viewer
- No collaborative features (multiple users)
- No backup/restore from the UI
- No analytics dashboard (trends over time, productivity patterns)

## Running

```bash
# MCP server (normal operation — started by Claude Code automatically via .mcp.json)
cd /Users/claude/memorable/plugin && python3 -m server --watch

# Web viewer
cd /Users/claude/memorable/plugin && python3 -m server.web --port 7777

# Process transcripts manually
cd /Users/claude/memorable/plugin && python3 -m server --process

# KG cleanup
cd /Users/claude/memorable/plugin && python3 cleanup_kg.py
```

## Design Principles

- **On-device first**: Minimize API calls. Use Apple ML frameworks where possible.
- **No build tools**: Vanilla HTML/CSS/JS for the web viewer. No npm, no webpack, no React.
- **Zero config**: Works out of the box with sensible defaults in config.py.
- **Non-blocking hooks**: Hook scripts must be fast. Heavy processing is deferred to the background watcher.
- **Graceful degradation**: If any ML framework is unavailable, features degrade rather than crash.

## For the Agent Team

You have creative freedom. Here are some ideas but don't feel limited to these:

**Server-side improvements:**
- Better startup seed that adapts to what the current session seems to be about
- Cross-session project tracking and grouping
- Smarter observation deduplication and summarization
- Analytics/insights from session data (trends, patterns, productivity)
- Better API endpoints for the web viewer (semantic search, filtering, pagination)
- Session tagging or categorization

**Web viewer improvements:**
- Analytics dashboard (charts, trends, activity heatmaps)
- Entity/observation editing and deletion from the UI
- Improved session detail view (conversation flow visualization)
- Export functionality (JSON, markdown)
- Better mobile experience
- Accessibility improvements

**New features:**
- Natural language querying ("what was I working on last Tuesday?")
- Automatic project detection from observation patterns
- "Related sessions" suggestions
- Session bookmarking or starring
- Notification/reminder system
- Diff view between sessions

**Important constraints:**
- macOS only (Apple ML frameworks)
- Python 3.10+
- No npm/build tools for the web viewer
- Don't break the MCP server protocol (JSON-RPC over stdio)
- Don't break the hook scripts (they must return quickly)
- The database schema can be extended but don't remove existing tables/columns
- Test by running `python3 -m server.web --port 7777` and checking in a browser
