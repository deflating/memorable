# Memorable: Files-First Architecture

*Written Feb 8, 2026. The current SQLite + libSQL sync approach is broken and over-engineered. This plan replaces it with files as the source of truth, synced by Syncthing, with a local-only disposable SQLite index for search.*

---

## The Problem

- libSQL embedded replicas are experimental (v0.0.50) and consistently break
- sqld adds a whole server to maintain on the Mac Mini
- SQLite databases can't be synced by Syncthing (concurrent writes corrupt)
- When the DB breaks, everything breaks — session notes, search, startup context
- We've spent more time debugging sync than building features

## The Principle

**The database is a cache, not the truth.**

Files are the canonical store. They sync via Syncthing (already working). Each machine builds its own local index from whatever files exist. If the index breaks, delete it and rebuild. Zero distributed systems.

---

## Canonical File Layout

Everything lives in `~/.memorable/data/` (Syncthing-synced):

```
~/.memorable/
├── data/                          # SYNCED via Syncthing — this is the truth
│   ├── sessions/                  # One .json file per session
│   │   ├── 2026-02-08-fixing-memorable.json
│   │   ├── 2026-02-08-session-summary-prompt.json
│   │   └── ...
│   ├── observations.jsonl         # Append-only event log
│   ├── prompts.jsonl              # Append-only user prompt log
│   └── sacred.json                # Manual important facts (priority 10)
│
├── index.db                       # LOCAL ONLY — disposable SQLite index
├── config.json                    # Local config
├── watcher.log                    # Local logs
└── .stignore                      # Tells Syncthing to skip index.db
```

### Session Files (`data/sessions/*.json`)

One file per processed session. Written once, never modified.

```json
{
  "id": "eed3efe9-f214-4300-b49c-ab401069441d",
  "date": "2026-02-08",
  "title": "Why aren't you reading your CLAUDE",
  "summary": "Matt spent this session trying to understand...",
  "message_count": 201,
  "word_count": 15048,
  "human_word_count": 6515,
  "source_machine": "macbookpro",
  "source_transcript": "eed3efe9-f214-4300-b49c-ab401069441d.jsonl",
  "processed_at": "2026-02-08T14:30:00Z"
}
```

**Why JSON not Markdown?** The summary is the only human-readable part. JSON is easier to parse for indexing and doesn't need YAML frontmatter parsing. The startup hook and web viewer can render it however they want.

**Filename:** `{date}-{slugified-title}.json`. Human-scannable in a file browser. Date prefix means `ls` gives chronological order.

### Observations Log (`data/observations.jsonl`)

Append-only. Each line is one observation from a PostToolUse hook.

```json
{"ts":"2026-02-08T14:30:00Z","session":"eed3efe9","type":"change","tool":"Edit","file":"server/processor.py","summary":"Edited processor.py","context":"fixing KG extraction"}
```

- Both machines can append safely — Syncthing handles append-only JSONL well
- If there's ever a conflict, Syncthing creates a `.sync-conflict` file, which is recoverable
- Lines are small (~200 bytes each), so the file stays manageable for months

### User Prompts Log (`data/prompts.jsonl`)

Same pattern. Each line is a captured user prompt.

```json
{"ts":"2026-02-08T14:32:00Z","session":"eed3efe9","prompt":"I'm feeling really upset about where things are at with Memorable","chars":60}
```

### Sacred Facts (`data/sacred.json`)

Manually curated. Replaces KG priority-10 entities.

```json
{
  "facts": [
    {"name": "Buddy", "description": "Matt's 10-year-old tabby cat", "added": "2026-02-01"},
    {"name": "Matt Kennelly", "description": "Primary developer, 33, Brisbane", "added": "2026-02-01"},
    {"name": "Memorable", "description": "Memory plugin system for Claude Code", "added": "2026-02-01"}
  ]
}
```

---

## Local Index (`index.db`)

SQLite database. **Never synced.** Listed in `.stignore`.

Built from the files in `data/`. Can be deleted and rebuilt at any time.

### Tables

```sql
-- Indexed from data/sessions/*.json
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,           -- from filename
    date TEXT,
    title TEXT,
    summary TEXT,
    message_count INTEGER,
    word_count INTEGER,
    human_word_count INTEGER,
    source_machine TEXT,
    processed_at TEXT,
    summary_embedding BLOB        -- computed locally via Apple NLEmbedding
);

-- Indexed from data/observations.jsonl
CREATE TABLE observations (
    rowid INTEGER PRIMARY KEY,
    ts TEXT,
    session_id TEXT,
    type TEXT,
    tool TEXT,
    file TEXT,
    summary TEXT,
    context TEXT
);

-- Indexed from data/prompts.jsonl
CREATE TABLE prompts (
    rowid INTEGER PRIMARY KEY,
    ts TEXT,
    session_id TEXT,
    prompt TEXT,
    prompt_embedding BLOB         -- computed locally
);

-- Rebuild tracking
CREATE TABLE index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- e.g. ("last_session_count", "33"), ("last_obs_offset", "820")
```

### Rebuild Logic

```python
def rebuild_index():
    """Rebuild local index from canonical files. Safe to run anytime."""
    # 1. Count session files in data/sessions/
    # 2. If count matches index_meta.last_session_count, skip (already up to date)
    # 3. Otherwise: clear sessions table, re-read all .json files, re-embed summaries
    # 4. For observations/prompts: read from last known offset, append new lines only
```

The rebuild is idempotent. Running it twice does nothing. Running it after deleting `index.db` rebuilds everything from scratch.

---

## What Changes in the Codebase

### processor.py
- `store_session()` → writes a .json file to `data/sessions/` instead of inserting into DB
- Remove all libSQL/sync_url logic
- Remove `processing_queue` table dependency — use filesystem (check if output file already exists)

### observer.py
- `store_observation()` → appends a line to `data/observations.jsonl`
- Remove `observations_queue` table — just append directly (fast enough)

### hooks/user_prompt.py
- Appends to `data/prompts.jsonl` instead of DB insert

### hooks/session_start.py
- Reads recent .json files from `data/sessions/` sorted by date
- Falls back to reading the local index for search results
- Sacred facts from `data/sacred.json`

### mcp_server.py
- `memorable_search_sessions` → queries local `index.db`
- `memorable_search_observations` → queries local `index.db`
- `memorable_get_system_status` → counts files + index stats
- `memorable_record_significant` → appends to `data/sacred.json`

### db.py
- Rewritten completely. No more libSQL. No more sync_url.
- Just plain `sqlite3.connect()` to a local index.db
- Add `rebuild_from_files()` method

### watcher.py
- Same as before: watches for JSONL transcripts, processes them
- No more DB-based processing queue — just check if output file exists
- Also watches `data/` for new files arriving via Syncthing → triggers index update

### web.py / UI
- No changes needed — just points at the local index instead of the synced DB

### Things to Remove
- `libsql_experimental` dependency
- `sync_url` / `auth_token` config
- `sqld` on the Mac Mini (launchd agent)
- `processing_queue` table
- `observations_queue` table
- `rolling_summaries` table (replace with reading recent session files directly)

---

## Migration Path

1. **Export current 33 sessions** from SQLite → `data/sessions/*.json` files
2. **Export observations** → `data/observations.jsonl`
3. **Export user_prompts** → `data/prompts.jsonl`
4. **Export sacred facts** → `data/sacred.json`
5. **Set up Syncthing** on `~/.memorable/data/` between both machines
6. **Add `.stignore`** for `index.db`, `*.log`, `config.json`
7. **Rewrite db.py** to be index-only
8. **Update processor.py** to write files
9. **Update hooks** to write files
10. **Kill sqld** on the Mini
11. **Remove libsql_experimental** from pyproject.toml
12. **Test rebuild** — delete index.db, verify it rebuilds from files

---

## What We Keep

- The Haiku summarization pipeline (fact sheet → summary) — it works well now
- The watcher daemon (watches for new transcripts)
- The hooks (session_start, user_prompt, post_tool_use, session_stop)
- The MCP tools interface
- The web viewer + API
- Apple NLEmbedding for semantic search
- GLiNER for metadata extraction (but only in the local index, not the canonical files)

## What We Drop

- libSQL embedded replicas
- sqld server
- Database sync of any kind
- `processing_queue` / `observations_queue` tables
- `compressed_50` column (dead code)
- KG extraction (already disabled, keep it disabled until we design it properly)

## What We Gain

- Sync that actually works (Syncthing on files)
- Human-readable canonical store (you can browse sessions in Finder)
- Disposable index (delete and rebuild anytime)
- No distributed systems to debug
- Simpler codebase (~200 lines of db.py removed)
- No sqld process eating memory on the Mini

---

## Open Questions

- **Do we keep GLiNER?** It's 200MB in memory. The only thing it does is extract entity names for metadata. We could skip it entirely and just use the summary text for search.
- **Rolling summary:** Currently built from session notes via the DB. In the new world, it would read the last N session .json files and concatenate summaries. Simpler but loses the "synthesis" step.
- **KG:** Shelved for now. When we revisit, per-entity JSON files (like ChatGPT suggested) is the right approach. Each entity = one file, synced independently.
- **Observation granularity:** Is one big `observations.jsonl` fine, or should it be per-day (`observations/2026-02-08.jsonl`)? Per-day is cleaner for Syncthing but more files to manage.

---

## Estimated Effort

This is a medium refactor. The processing pipeline, hooks, and UI stay mostly the same. The main work is:

1. New file I/O layer (write sessions/observations to files) — ~2 hours
2. Rewrite db.py as a local index builder — ~2 hours
3. Update hooks and MCP server — ~1 hour
4. Migration script (export current DB → files) — ~30 min
5. Syncthing setup on `~/.memorable/data/` — ~15 min
6. Kill sqld, remove libsql — ~15 min
7. Testing — ~1 hour

Total: roughly one focused session.
