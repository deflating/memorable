"""SQLite database layer for Memorable.

Stores: sessions (with compressed transcripts at two tiers),
knowledge graph (entities + relationships), context seeds,
and processing queue.
"""

import sqlite3
import json
import time
from pathlib import Path
from contextlib import contextmanager


DEFAULT_DB_PATH = Path.home() / ".memorable" / "memorable.db"


class MemorableDB:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ── Sessions ──────────────────────────────────────────────

    def store_session(self, transcript_id: str, date: str, title: str,
                      summary: str, header: str, compressed_50: str,
                      source_path: str = "", message_count: int = 0,
                      word_count: int = 0, human_word_count: int = 0) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO sessions
                   (transcript_id, date, title, summary, header, compressed_50,
                    source_path, message_count, word_count, human_word_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (transcript_id, date, title, summary, header, compressed_50,
                 source_path, message_count, word_count, human_word_count)
            )
            return cur.lastrowid

    def search_sessions(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword search across summaries, compressed transcripts, and titles."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, transcript_id, date, title, summary, header,
                          compressed_50, message_count, word_count
                   FROM sessions
                   WHERE summary LIKE ? OR compressed_50 LIKE ? OR title LIKE ?
                   ORDER BY date DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_sessions(self, days: int = 5, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            cutoff = time.time() - (days * 86400)
            rows = conn.execute(
                """SELECT id, transcript_id, date, title, summary, header,
                          message_count, word_count
                   FROM sessions
                   WHERE created_at > ?
                   ORDER BY date DESC
                   LIMIT ?""",
                (cutoff, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_summaries(self, limit: int = 10) -> list[dict]:
        """Get recent session summaries for startup seed."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, date, title, summary, header,
                          message_count, word_count
                   FROM sessions
                   ORDER BY date DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Knowledge Graph ───────────────────────────────────────

    def add_entity(self, name: str, entity_type: str, description: str = "",
                   priority: int = 5, metadata: dict | None = None) -> int:
        with self._conn() as conn:
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

    def add_relationship(self, source_name: str, source_type: str,
                         rel_type: str, target_name: str, target_type: str,
                         description: str = "", confidence: float = 1.0) -> int:
        with self._conn() as conn:
            # Get or create entities
            source_id = self._get_or_create_entity(conn, source_name, source_type)
            target_id = self._get_or_create_entity(conn, target_name, target_type)
            cur = conn.execute(
                """INSERT INTO kg_relationships
                   (source_id, target_id, rel_type, description, confidence)
                   VALUES (?, ?, ?, ?, ?)""",
                (source_id, target_id, rel_type, description, confidence)
            )
            return cur.lastrowid

    def _get_or_create_entity(self, conn, name: str, entity_type: str) -> int:
        row = conn.execute(
            "SELECT id FROM kg_entities WHERE name = ? AND type = ?",
            (name, entity_type)
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO kg_entities (name, type) VALUES (?, ?)",
            (name, entity_type)
        )
        return cur.lastrowid

    def query_kg(self, entity: str | None = None, entity_type: str | None = None,
                 rel_type: str | None = None, min_priority: int = 0,
                 limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            if entity:
                # Get entity and its relationships
                rows = conn.execute(
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
                ).fetchall()
            elif entity_type:
                rows = conn.execute(
                    """SELECT name, type, description, priority, metadata
                       FROM kg_entities
                       WHERE type = ? AND priority >= ?
                       ORDER BY priority DESC
                       LIMIT ?""",
                    (entity_type, min_priority, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT name, type, description, priority, metadata
                       FROM kg_entities
                       WHERE priority >= ?
                       ORDER BY priority DESC
                       LIMIT ?""",
                    (min_priority, limit)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_sacred_facts(self) -> list[dict]:
        """Get all priority-10 (sacred, immutable) facts."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT name, type, description, metadata
                   FROM kg_entities WHERE priority = 10
                   ORDER BY name"""
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Context Seeds ─────────────────────────────────────────

    def store_context_seed(self, session_id: str, seed_content: str,
                           seed_type: str = "live") -> int:
        """Store a context seed (live mid-session or startup)."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO context_seeds (session_id, seed_content, seed_type)
                   VALUES (?, ?, ?)""",
                (session_id, seed_content, seed_type)
            )
            return cur.lastrowid

    def get_last_context_seed(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT session_id, seed_content, seed_type, created_at
                   FROM context_seeds
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
            return dict(row) if row else None

    # ── Processing Queue ──────────────────────────────────────

    def queue_transcript(self, transcript_path: str, file_hash: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO processing_queue
                   (transcript_path, file_hash, status)
                   VALUES (?, ?, 'pending')""",
                (transcript_path, file_hash)
            )
            return cur.lastrowid

    def get_pending_transcripts(self, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, transcript_path, file_hash
                   FROM processing_queue
                   WHERE status = 'pending'
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_processed(self, queue_id: int, session_id: int | None = None,
                       error: str | None = None):
        status = "error" if error else "done"
        with self._conn() as conn:
            conn.execute(
                """UPDATE processing_queue
                   SET status = ?, session_id = ?, error = ?, processed_at = ?
                   WHERE id = ?""",
                (status, session_id, error, time.time(), queue_id)
            )

    def is_transcript_processed(self, file_hash: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM processing_queue WHERE file_hash = ? AND status = 'done'",
                (file_hash,)
            ).fetchone()
            return row is not None

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._conn() as conn:
            sessions = conn.execute("SELECT COUNT(*) as n FROM sessions").fetchone()["n"]
            entities = conn.execute("SELECT COUNT(*) as n FROM kg_entities").fetchone()["n"]
            relationships = conn.execute("SELECT COUNT(*) as n FROM kg_relationships").fetchone()["n"]
            sacred = conn.execute("SELECT COUNT(*) as n FROM kg_entities WHERE priority = 10").fetchone()["n"]
            seeds = conn.execute("SELECT COUNT(*) as n FROM context_seeds").fetchone()["n"]
            pending = conn.execute("SELECT COUNT(*) as n FROM processing_queue WHERE status = 'pending'").fetchone()["n"]
            total_words = conn.execute("SELECT COALESCE(SUM(word_count), 0) as n FROM sessions").fetchone()["n"]
            return {
                "sessions": sessions,
                "kg_entities": entities,
                "kg_relationships": relationships,
                "sacred_facts": sacred,
                "context_seeds": seeds,
                "pending_transcripts": pending,
                "total_words_processed": total_words,
            }


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
"""
