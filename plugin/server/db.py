"""SQLite database layer for Memorable.

Uses libsql_experimental for embedded replica support — each machine
keeps a local SQLite file that syncs with a central sqld server.
When sync_url is not configured, falls back to plain local SQLite.

Stores: sessions (with compressed transcripts), knowledge graph
(entities + relationships), context seeds, and processing queue.
"""

import json
import time
from pathlib import Path

try:
    import libsql_experimental as libsql
    HAS_LIBSQL = True
except ImportError:
    HAS_LIBSQL = False

DEFAULT_DB_PATH = Path.home() / ".memorable" / "memorable.db"


def _rows_to_dicts(cursor) -> list[dict]:
    """Convert cursor results to list of dicts using cursor.description."""
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _row_to_dict(cursor) -> dict | None:
    """Fetch one row as a dict."""
    if not cursor.description:
        return None
    cols = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


class MemorableDB:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH,
                 sync_url: str = "", auth_token: str = ""):
        self.db_path = db_path
        self.sync_url = sync_url
        self.auth_token = auth_token
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        """Open a connection — embedded replica if sync_url is set, local otherwise."""
        if self.sync_url and HAS_LIBSQL:
            try:
                kwargs = {"sync_url": self.sync_url}
                if self.auth_token:
                    kwargs["auth_token"] = self.auth_token
                conn = libsql.connect(str(self.db_path), **kwargs)
                return conn
            except (ValueError, Exception):
                # Sync server unreachable — fall back to local-only
                pass
        if HAS_LIBSQL:
            conn = libsql.connect(str(self.db_path))
        else:
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _sync(self, conn):
        """Sync with server if this is an embedded replica."""
        if self.sync_url and hasattr(conn, "sync"):
            try:
                conn.sync()
            except Exception:
                pass  # offline — local replica still works

    def _execute(self, callback):
        """Execute a callback with a connection, handling sync and cleanup."""
        conn = self._connect()
        try:
            self._sync(conn)
            result = callback(conn)
            conn.commit()
            self._sync(conn)
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _query(self, callback):
        """Execute a read-only callback — syncs before reading."""
        conn = self._connect()
        try:
            self._sync(conn)
            return callback(conn)
        finally:
            conn.close()

    def _init_schema(self):
        def init(conn):
            conn.executescript(SCHEMA)
        self._execute(init)

    # ── Sessions ──────────────────────────────────────────────

    def store_session(self, transcript_id: str, date: str, title: str,
                      summary: str, header: str, compressed_50: str,
                      source_path: str = "", message_count: int = 0,
                      word_count: int = 0, human_word_count: int = 0,
                      metadata: str = "{}") -> int:
        def do(conn):
            cur = conn.execute(
                """INSERT INTO sessions
                   (transcript_id, date, title, summary, header, compressed_50,
                    metadata, source_path, message_count, word_count, human_word_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (transcript_id, date, title, summary, header, compressed_50,
                 metadata, source_path, message_count, word_count, human_word_count)
            )
            return cur.lastrowid
        return self._execute(do)

    def search_sessions(self, query: str, limit: int = 10) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, transcript_id, date, title, summary, header,
                          compressed_50, message_count, word_count
                   FROM sessions
                   WHERE summary LIKE ? OR compressed_50 LIKE ? OR title LIKE ?
                   ORDER BY date DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_recent_sessions(self, days: int = 5, limit: int = 20) -> list[dict]:
        def do(conn):
            cutoff = time.time() - (days * 86400)
            cur = conn.execute(
                """SELECT id, transcript_id, date, title, summary, header,
                          message_count, word_count
                   FROM sessions
                   WHERE created_at > ?
                   ORDER BY date DESC
                   LIMIT ?""",
                (cutoff, limit)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_recent_summaries(self, limit: int = 10) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, transcript_id, date, title, summary, header,
                          message_count, word_count
                   FROM sessions
                   ORDER BY date DESC
                   LIMIT ?""",
                (limit,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_session_by_transcript_id(self, transcript_id: str) -> dict | None:
        def do(conn):
            cur = conn.execute(
                """SELECT id, transcript_id, date, title, summary, header,
                          compressed_50, message_count, word_count
                   FROM sessions
                   WHERE transcript_id = ?""",
                (transcript_id,)
            )
            return _row_to_dict(cur)
        return self._query(do)

    # ── Knowledge Graph ───────────────────────────────────────

    def add_entity(self, name: str, entity_type: str, description: str = "",
                   priority: int = 5, metadata: dict | None = None) -> int:
        def do(conn):
            cur = conn.execute(
                """INSERT INTO kg_entities (name, type, description, priority, metadata)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(name, type) DO UPDATE SET
                     description = excluded.description,
                     priority = CASE
                       WHEN kg_entities.priority = 10 THEN 10
                       ELSE excluded.priority
                     END,
                     metadata = excluded.metadata,
                     updated_at = ?""",
                (name, entity_type, description, priority,
                 json.dumps(metadata or {}), time.time())
            )
            return cur.lastrowid
        return self._execute(do)

    def add_relationship(self, source_name: str, source_type: str,
                         rel_type: str, target_name: str, target_type: str,
                         description: str = "", confidence: float = 1.0) -> int:
        def do(conn):
            source_id = self._get_or_create_entity_inner(conn, source_name, source_type)
            target_id = self._get_or_create_entity_inner(conn, target_name, target_type)
            cur = conn.execute(
                """INSERT INTO kg_relationships
                   (source_id, target_id, rel_type, description, confidence)
                   VALUES (?, ?, ?, ?, ?)""",
                (source_id, target_id, rel_type, description, confidence)
            )
            return cur.lastrowid
        return self._execute(do)

    def _get_or_create_entity_inner(self, conn, name: str, entity_type: str) -> int:
        row = conn.execute(
            "SELECT id FROM kg_entities WHERE name = ? AND type = ?",
            (name, entity_type)
        ).fetchone()
        if row:
            return row[0]
        cur = conn.execute(
            "INSERT INTO kg_entities (name, type) VALUES (?, ?)",
            (name, entity_type)
        )
        return cur.lastrowid

    def query_kg(self, entity: str | None = None, entity_type: str | None = None,
                 rel_type: str | None = None, min_priority: int = 0,
                 limit: int = 50) -> list[dict]:
        def do(conn):
            if entity:
                cur = conn.execute(
                    """SELECT e.name, e.type, e.description, e.priority, e.metadata,
                              r.rel_type, t.name as target_name, t.type as target_type,
                              r.description as rel_description
                       FROM kg_entities e
                       LEFT JOIN kg_relationships r ON e.id = r.source_id
                       LEFT JOIN kg_entities t ON r.target_id = t.id
                       WHERE e.name LIKE ? AND e.priority >= ?
                       ORDER BY e.priority DESC
                       LIMIT ?""",
                    (f"%{entity}%", min_priority, limit)
                )
            elif entity_type:
                cur = conn.execute(
                    """SELECT name, type, description, priority, metadata
                       FROM kg_entities
                       WHERE type = ? AND priority >= ?
                       ORDER BY priority DESC
                       LIMIT ?""",
                    (entity_type, min_priority, limit)
                )
            else:
                cur = conn.execute(
                    """SELECT name, type, description, priority, metadata
                       FROM kg_entities
                       WHERE priority >= ?
                       ORDER BY priority DESC
                       LIMIT ?""",
                    (min_priority, limit)
                )
            return _rows_to_dicts(cur)
        return self._query(do)

    def delete_entity(self, entity_id: int):
        """Delete an entity and its relationships."""
        def do(conn):
            conn.execute("DELETE FROM kg_relationships WHERE source_id = ? OR target_id = ?",
                         (entity_id, entity_id))
            conn.execute("DELETE FROM kg_entities WHERE id = ?", (entity_id,))
        self._execute(do)

    def delete_entities_below_priority(self, min_priority: int) -> int:
        """Delete all entities (and their relationships) below a priority threshold.
        Returns count of deleted entities."""
        def do(conn):
            # Get IDs to delete
            rows = conn.execute(
                "SELECT id FROM kg_entities WHERE priority < ?", (min_priority,)
            ).fetchall()
            ids = [r[0] for r in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM kg_relationships WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                         ids + ids)
            conn.execute(f"DELETE FROM kg_entities WHERE id IN ({placeholders})", ids)
            return len(ids)
        return self._execute(do)

    def get_all_entities(self, limit: int = 5000) -> list[dict]:
        """Get all entities with their IDs for cleanup operations."""
        def do(conn):
            cur = conn.execute(
                """SELECT id, name, type, description, priority
                   FROM kg_entities ORDER BY priority DESC, name LIMIT ?""",
                (limit,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_sacred_facts(self) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT name, type, description, metadata
                   FROM kg_entities WHERE priority = 10
                   ORDER BY name"""
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_kg_graph(self, min_priority: int = 0) -> dict:
        """Get full KG as nodes + edges for graph visualization."""
        def do(conn):
            entities = conn.execute(
                """SELECT id, name, type, description, priority
                   FROM kg_entities
                   WHERE priority >= ?
                   ORDER BY priority DESC""",
                (min_priority,)
            ).fetchall()
            cols_e = ["id", "name", "type", "description", "priority"]
            nodes = [dict(zip(cols_e, row)) for row in entities]

            rels = conn.execute(
                """SELECT r.id, s.name as source, s.type as source_type,
                          r.rel_type, t.name as target, t.type as target_type
                   FROM kg_relationships r
                   JOIN kg_entities s ON r.source_id = s.id
                   JOIN kg_entities t ON r.target_id = t.id"""
            ).fetchall()
            cols_r = ["id", "source", "source_type", "rel_type", "target", "target_type"]
            edges = [dict(zip(cols_r, row)) for row in rels]

            return {"nodes": nodes, "edges": edges}
        return self._query(do)

    # ── Context Seeds ─────────────────────────────────────────

    def store_context_seed(self, session_id: str, seed_content: str,
                           seed_type: str = "live") -> int:
        def do(conn):
            cur = conn.execute(
                """INSERT INTO context_seeds (session_id, seed_content, seed_type)
                   VALUES (?, ?, ?)""",
                (session_id, seed_content, seed_type)
            )
            return cur.lastrowid
        return self._execute(do)

    def get_last_context_seed(self) -> dict | None:
        def do(conn):
            cur = conn.execute(
                """SELECT session_id, seed_content, seed_type, created_at
                   FROM context_seeds
                   ORDER BY created_at DESC LIMIT 1"""
            )
            return _row_to_dict(cur)
        return self._query(do)

    # ── Processing Queue ──────────────────────────────────────

    def queue_transcript(self, transcript_path: str, file_hash: str) -> int:
        def do(conn):
            cur = conn.execute(
                """INSERT OR IGNORE INTO processing_queue
                   (transcript_path, file_hash, status)
                   VALUES (?, ?, 'pending')""",
                (transcript_path, file_hash)
            )
            return cur.lastrowid
        return self._execute(do)

    def get_pending_transcripts(self, limit: int = 10) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, transcript_path, file_hash
                   FROM processing_queue
                   WHERE status = 'pending'
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (limit,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def mark_processed(self, queue_id: int, session_id: int | None = None,
                       error: str | None = None):
        status = "error" if error else "done"
        def do(conn):
            conn.execute(
                """UPDATE processing_queue
                   SET status = ?, session_id = ?, error = ?, processed_at = ?
                   WHERE id = ?""",
                (status, session_id, error, time.time(), queue_id)
            )
        self._execute(do)

    def is_transcript_processed(self, file_hash: str) -> bool:
        def do(conn):
            row = conn.execute(
                "SELECT id FROM processing_queue WHERE file_hash = ? AND status = 'done'",
                (file_hash,)
            ).fetchone()
            return row is not None
        return self._query(do)

    # ── Observations ─────────────────────────────────────────

    def queue_observation(self, session_id: str, tool_name: str,
                          tool_input: str, tool_response: str,
                          context_before: str = "", context_after: str = "",
                          cwd: str = "") -> int:
        def do(conn):
            cur = conn.execute(
                """INSERT INTO observations_queue
                   (session_id, tool_name, tool_input, tool_response,
                    context_before, context_after, cwd)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, tool_name, tool_input, tool_response,
                 context_before, context_after, cwd)
            )
            return cur.lastrowid
        return self._execute(do)

    def get_pending_observations(self, limit: int = 50) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, session_id, tool_name, tool_input, tool_response,
                          context_before, context_after, cwd, created_at
                   FROM observations_queue
                   WHERE status = 'pending'
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (limit,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def mark_observation_queued(self, queue_id: int, observation_id: int | None = None):
        status = "processed" if observation_id else "skipped"
        def do(conn):
            conn.execute(
                """UPDATE observations_queue
                   SET status = ?, processed_at = ?
                   WHERE id = ?""",
                (status, time.time(), queue_id)
            )
        self._execute(do)

    def store_observation(self, session_id: str, obs_type: str, title: str,
                          summary: str, files: str = "[]",
                          embedding: bytes | None = None,
                          tool_name: str = "") -> int:
        def do(conn):
            cur = conn.execute(
                """INSERT INTO observations
                   (session_id, observation_type, title, summary, files,
                    embedding, tool_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, obs_type, title, summary, files,
                 embedding, tool_name)
            )
            return cur.lastrowid
        return self._execute(do)

    def get_observations_by_session(self, session_id: str,
                                     limit: int = 50) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, session_id, observation_type, title, summary,
                          files, tool_name, created_at
                   FROM observations
                   WHERE session_id = ?
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (session_id, limit)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def search_observations_keyword(self, query: str,
                                     limit: int = 20) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, session_id, observation_type, title, summary,
                          files, tool_name, created_at
                   FROM observations
                   WHERE title LIKE ? OR summary LIKE ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_recent_observations(self, limit: int = 50) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, session_id, observation_type, title, summary,
                          files, tool_name, created_at
                   FROM observations
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_all_observation_texts(self, limit: int = 5000) -> list[dict]:
        """Get id + title + summary for all observations (for semantic search)."""
        def do(conn):
            cur = conn.execute(
                """SELECT id, session_id, observation_type, title, summary,
                          files, tool_name, created_at
                   FROM observations
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_timeline(self, limit: int = 100) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT 'observation' as kind, id, session_id,
                          observation_type, title, summary, files,
                          tool_name, NULL as prompt_number,
                          NULL as prompt_text, created_at
                   FROM observations
                   UNION ALL
                   SELECT 'prompt' as kind, id, session_id,
                          NULL, NULL, NULL, NULL,
                          NULL, prompt_number,
                          prompt_text, created_at
                   FROM user_prompts
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    # ── User Prompts ─────────────────────────────────────────

    def store_user_prompt(self, session_id: str, prompt_number: int,
                          prompt_text: str, embedding: bytes | None = None) -> int:
        def do(conn):
            cur = conn.execute(
                """INSERT INTO user_prompts
                   (session_id, prompt_number, prompt_text, embedding)
                   VALUES (?, ?, ?, ?)""",
                (session_id, prompt_number, prompt_text, embedding)
            )
            return cur.lastrowid
        return self._execute(do)

    def search_user_prompts(self, query: str, limit: int = 20) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, session_id, prompt_number, prompt_text, created_at
                   FROM user_prompts
                   WHERE prompt_text LIKE ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (f"%{query}%", limit)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_user_prompts_by_session(self, session_id: str) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT id, prompt_number, prompt_text, created_at
                   FROM user_prompts
                   WHERE session_id = ?
                   ORDER BY prompt_number ASC""",
                (session_id,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_prompt_count_for_session(self, session_id: str) -> int:
        def do(conn):
            row = conn.execute(
                "SELECT COUNT(*) FROM user_prompts WHERE session_id = ?",
                (session_id,)
            ).fetchone()
            return row[0] if row else 0
        return self._query(do)

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> dict:
        def do(conn):
            def count(sql):
                return conn.execute(sql).fetchone()[0]
            return {
                "sessions": count("SELECT COUNT(*) FROM sessions"),
                "kg_entities": count("SELECT COUNT(*) FROM kg_entities"),
                "kg_relationships": count("SELECT COUNT(*) FROM kg_relationships"),
                "sacred_facts": count("SELECT COUNT(*) FROM kg_entities WHERE priority = 10"),
                "context_seeds": count("SELECT COUNT(*) FROM context_seeds"),
                "pending_transcripts": count("SELECT COUNT(*) FROM processing_queue WHERE status = 'pending'"),
                "total_words_processed": count("SELECT COALESCE(SUM(word_count), 0) FROM sessions"),
                "observations": count("SELECT COUNT(*) FROM observations"),
                "pending_observations": count("SELECT COUNT(*) FROM observations_queue WHERE status = 'pending'"),
                "user_prompts": count("SELECT COUNT(*) FROM user_prompts"),
            }
        return self._query(do)


# ── Schema ────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id TEXT UNIQUE,
    date TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    header TEXT NOT NULL DEFAULT '',
    compressed_50 TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    source_path TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    human_word_count INTEGER DEFAULT 0,
    created_at REAL DEFAULT (unixepoch('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);

CREATE TABLE IF NOT EXISTS kg_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT DEFAULT '',
    priority INTEGER DEFAULT 5 CHECK(priority BETWEEN 1 AND 10),
    metadata TEXT DEFAULT '{}',
    created_at REAL DEFAULT (unixepoch('now')),
    updated_at REAL DEFAULT (unixepoch('now')),
    UNIQUE(name, type)
);

CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(type);
CREATE INDEX IF NOT EXISTS idx_kg_entities_priority ON kg_entities(priority);

CREATE TABLE IF NOT EXISTS kg_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES kg_entities(id),
    target_id INTEGER NOT NULL REFERENCES kg_entities(id),
    rel_type TEXT NOT NULL,
    description TEXT DEFAULT '',
    confidence REAL DEFAULT 1.0,
    created_at REAL DEFAULT (unixepoch('now'))
);

CREATE INDEX IF NOT EXISTS idx_kg_rel_source ON kg_relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_kg_rel_target ON kg_relationships(target_id);

CREATE TABLE IF NOT EXISTS context_seeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seed_content TEXT NOT NULL,
    seed_type TEXT DEFAULT 'live',
    created_at REAL DEFAULT (unixepoch('now'))
);

CREATE INDEX IF NOT EXISTS idx_seeds_created ON context_seeds(created_at);

CREATE TABLE IF NOT EXISTS processing_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_path TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'pending',
    session_id INTEGER REFERENCES sessions(id),
    error TEXT,
    created_at REAL DEFAULT (unixepoch('now')),
    processed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON processing_queue(status);

CREATE TABLE IF NOT EXISTS rolling_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_type TEXT NOT NULL,
    content TEXT NOT NULL,
    days_covered INTEGER DEFAULT 5,
    session_count INTEGER DEFAULT 0,
    created_at REAL DEFAULT (unixepoch('now'))
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    observation_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    files TEXT NOT NULL DEFAULT '[]',
    embedding BLOB,
    tool_name TEXT,
    created_at REAL DEFAULT (unixepoch('now'))
);

CREATE INDEX IF NOT EXISTS idx_observations_session ON observations(session_id);
CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_observations_created ON observations(created_at);

CREATE TABLE IF NOT EXISTS observations_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input TEXT NOT NULL,
    tool_response TEXT NOT NULL,
    context_before TEXT NOT NULL DEFAULT '',
    context_after TEXT NOT NULL DEFAULT '',
    cwd TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at REAL DEFAULT (unixepoch('now')),
    processed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_obs_queue_status ON observations_queue(status);
CREATE INDEX IF NOT EXISTS idx_obs_queue_session ON observations_queue(session_id);

CREATE TABLE IF NOT EXISTS user_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    prompt_number INTEGER NOT NULL,
    prompt_text TEXT NOT NULL,
    embedding BLOB,
    created_at REAL DEFAULT (unixepoch('now'))
);

CREATE INDEX IF NOT EXISTS idx_user_prompts_session ON user_prompts(session_id);
CREATE INDEX IF NOT EXISTS idx_user_prompts_created ON user_prompts(created_at);
"""
