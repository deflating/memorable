# Memorable Code Review — Five Perspectives

*Generated 2026-02-08 by a team of 5 review agents*

---

## Overall Verdict

**The codebase is surprisingly clean for a 6-month-old project.** ~5000 lines of Python, well-structured, mostly justified complexity. The architecture is fundamentally sound. The issues below are about hardening, scaling, and closing the gap between "session logger" and "memory system."

---

## Top 10 Findings (Cross-Cutting)

These are the findings that multiple reviewers flagged or that have the highest impact.

### 1. Semantic Search Won't Scale (Performance + Architect)
**Current:** Search loads ALL sessions into memory and computes embeddings on every query. O(n) per search.
- 76 sessions: ~300ms (fine)
- 1,000 sessions: 5-10s (bad)
- 10,000 sessions: unusable

**Fix:** Pre-embed session summaries (add embedding column), embed query once, use vector comparison. Consider FTS5 for keyword search.

### 2. Startup Seed Doesn't Surface the Good Stuff (Product)
The session notes pipeline now produces excellent structured notes (summary, key moments, mood, future notes). But **none of this is shown at startup**. Claude gets emoji tags and a truncated rolling summary instead.
- compressed_50 summaries exist but aren't in the startup seed
- Rolling summary truncated to 800 chars (too aggressive)
- No "unfinished work" callout from previous sessions

**Fix:** Show compressed_50 for recent sessions. Remove 800-char limit. Add "Unfinished" section from session notes.

### 3. Dual Summarization Pipeline — Paying Twice (Skeptic)
Both the old pipeline (`_extract_conversation` → raw text → Haiku) and the new pipeline (`extract_session_data` → fact sheet → Haiku) run on every session. Two Haiku calls for one summary.

**Fix:** Delete the old pipeline. Use only the fact sheet approach.

### 4. Hook Scripts Can Crash Claude Code (Reliability)
Hook scripts have minimal error handling. If session_start crashes (DB locked, embedding fails), the entire Claude Code session is blocked.

**Fix:** Wrap all hook operations in try/except. Always return gracefully. Log errors to file.

### 5. No Retry for Failed Transcripts (Reliability + Skeptic)
If Haiku is temporarily unavailable, the transcript is marked with error and **never retried**. Sessions are permanently lost from memory.

**Fix:** Allow reprocessing of error-status transcripts. Add retry with backoff.

### 6. Context Seeds Table — Dead Code (Skeptic)
`context_seeds` table has store/get methods but zero callers anywhere in the codebase. An abandoned experiment.

**Fix:** Delete the table, delete the methods.

### 7. LibSQL Sync — Built but Never Used (Skeptic + Architect)
~60 lines of sync infrastructure (auth tokens, retry logic, offline fallback) with no documentation and no evidence it's ever been enabled.

**Fix:** Either document and test it, or delete it.

### 8. Missing Logging Infrastructure (Architect + Reliability)
All logging is `print()` to stdout/stderr. No levels, no timestamps, no file output, no way to diagnose issues after the fact.

**Fix:** Use Python `logging` module. Log to ~/.memorable/memorable.log with rotation.

### 9. No Database Transactions for Multi-Step Operations (Reliability)
`store_session()` and `mark_processed()` are separate calls. If the second fails, you get orphaned queue entries or duplicate sessions.

**Fix:** Wrap multi-step DB operations in transactions.

### 10. Observation Queue Is Overbuilt (Skeptic)
The async observation queue adds ~100 lines and a background thread, but the hook already runs in the background. Processing could be synchronous.

**Fix:** Consider simplifying to synchronous processing, or at minimum document why async exists.

---

## By Reviewer

### The Architect — Code Organization & Design
**Rating:** Fundamentally sound, needs hardening

**Key findings:**
- Business logic scattered across layers (filtering heuristics in 4 different files)
- Tight coupling to ML implementations (can't swap models without editing 3 files)
- Config object passed everywhere instead of injecting specific values
- Missing service layer — no clear "use case" boundaries
- Clean database abstraction (well done)
- Good MCP tool boundaries (easy to extend)
- Observation data passed as dicts everywhere — should be dataclasses

**Top recommendation:** Abstract ML providers behind interfaces. Enables testing, model swapping, fallback chains.

### The Skeptic — Overengineering & Dead Code
**Rating:** 80% clean, 20% migration artifacts

**Key findings:**
- LibSQL sync: built, never used (~60 lines)
- Context seeds table: dead code (zero callers)
- import_sessions.py: one-time migration script still in server/
- Dual summarization pipeline: paying for 2 Haiku calls
- Observation queue: overbuilt for current needs
- Tool skip lists duplicated in observer.py and notes.py
- db.py has near-duplicate query methods (~15% bloat)

**What's NOT overengineered (defended):**
- KG extraction pipeline (each step serves a purpose)
- Apple ML wrappers (no simpler alternative)
- Observation type classification (used by web UI filters)
- Hybrid search (keyword + semantic catches different things)

**Verdict:** "For a 6-month-old project with ~5000 lines, this is impressively tight."

### The Reliability Engineer — Error Handling & Failure Modes
**Rating:** Optimized for the happy path, fragile on errors

**Critical findings (3):**
- Hook crashes can break Claude Code sessions
- DB connection failures silent in MCP server (no reconnection)
- Watcher background thread can die silently

**Important findings (9):**
- Race condition in observation queue (no processing lock)
- No rollback on partial KG extraction failures
- Failed transcripts never retried
- NULL embeddings break semantic search
- Orphaned queue entries from partial failures
- Config file parse errors crash everything
- LLM CLI failures return silent empty strings
- AFM context window errors swallowed
- No timeout on database operations

**Top recommendation:** "Add comprehensive logging, use database transactions, implement circuit breakers for external dependencies."

### The Product Mind — UX & Feature Gaps
**Rating:** Great infrastructure, presentation needs to catch up

**Key findings:**
- Startup seed shows metadata, not memory (emoji tags instead of what happened)
- Session notes are rich but hidden (not in startup seed or web viewer)
- Web viewer search is keyword-only (MCP tools have hybrid)
- No project/thread grouping across sessions
- No "continue session" intelligence (detecting unfinished work)
- No temporal decay on KG entities (old entities stay forever)
- No export from web viewer

**The core tension:** "Memorable treats memory like a database. But memory is narrative."

**For Matt's SDAM:** "He can't fill in the narrative gaps himself. The system has to do it."

**Top recommendation:** "Fix startup seed to show compressed_50 summaries and unfinished work. This is the highest-impact change for user experience."

### The Performance Auditor — Speed & Scaling
**Rating:** Good at current scale (76 sessions), won't survive 1,000+

**Critical findings:**
- Semantic search is O(n) — scans all sessions per query
- session_start hook can block for 5-15s (if rolling summary needs regeneration)
- GLiNER model (~200MB) loaded on-demand, never unloaded

**Measurements:**
- NLEmbedding latency: 38.6ms per embedding
- JSONL parsing: 21ms for 1,750 lines
- DB size: 824KB (76 sessions, 439 entities)
- Observation count: 0 (hooks appear disabled)

**Scaling projections:**
| Metric | Now | @ 1K sessions | @ 10K sessions |
|--------|-----|---------------|----------------|
| DB size | 824KB | ~50MB | ~500MB |
| Search latency | 300ms | 5-10s | 50-100s |
| Startup hook | 50-150ms | 200-500ms | 1-2s |

**Quick wins (< 30 min each):**
1. Add WHERE clause to timeline query (SQL not Python filter)
2. Cache startup stats (update every 5min)
3. Embed query once in search (50% faster)
4. Limit observations in session_stop hook (prevent 500ms+ hangs)

**Top recommendation:** "Pre-embed sessions and fix search algorithm before hitting 1,000 sessions."

---

## Consolidated Action Items

### Do Now (High Impact, Low Effort)
1. Delete old summarization pipeline (saves 1 Haiku call/session + 80 lines)
2. Delete context_seeds dead code (cleanup)
3. Wrap hook scripts in try/except (prevent Claude Code crashes)
4. Add WHERE clause to timeline SQL query (quick win)
5. Show compressed_50 in startup seed (highest UX impact)

### Do Soon (High Impact, Medium Effort)
6. Pre-embed session summaries for search (scaling prerequisite)
7. Fix search to embed query once (50% faster)
8. Add retry logic for failed transcripts
9. Replace print() with logging module
10. Use DB transactions for multi-step operations

### Do Eventually (Medium Impact)
11. Add FTS5 for keyword search
12. Document or delete LibSQL sync code
13. Abstract ML providers behind interfaces
14. Add project threading / cross-session grouping
15. Consolidate duplicate DB query methods

### Consider (Low Impact)
16. Simplify observation queue (make synchronous)
17. Archive import_sessions.py
18. Consolidate tool skip lists
19. Add health check endpoint
20. Move to dataclasses for observations/sessions
