"""Local index builder for Memorable.

Reads canonical files from ~/.memorable/data/ and builds a local-only
SQLite index for fast search. The index is disposable — delete it and
rebuild from files at any time.

No libSQL. No sync. No distributed anything.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
DEFAULT_INDEX_PATH = Path.home() / ".memorable" / "index.db"


class MemorableDB:
    """Local-only SQLite index built from canonical files."""

    def __init__(self, index_path: Path = DEFAULT_INDEX_PATH, **kwargs):
        # Accept and ignore legacy kwargs (sync_url, auth_token)
        self.index_path = index_path
        index_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(str(self.index_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _execute(self, callback):
        conn = self._connect()
        try:
            result = callback(conn)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _query(self, callback):
        conn = self._connect()
        try:
            return callback(conn)
        finally:
            conn.close()

    def _init_schema(self):
        def init(conn):
            conn.executescript(SCHEMA)
        self._execute(init)

    # ── Rebuild from Files ───────────────────────────────────

    def rebuild_from_files(self):
        """Rebuild the entire index from canonical files in data/."""
        logger.info("Rebuilding index from files...")
        self._rebuild_sessions()
        self._rebuild_observations()
        self._rebuild_prompts()
        logger.info("Index rebuild complete.")

    def rebuild_if_needed(self):
        """Rebuild only if file counts don't match index counts."""
        session_files = list(SESSIONS_DIR.glob("*.json")) if SESSIONS_DIR.exists() else []
        indexed_count = self._query(
            lambda conn: conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        )
        if len(session_files) != indexed_count:
            self.rebuild_from_files()

    def _rebuild_sessions(self):
        """Re-index all session JSON files."""
        def do(conn):
            conn.execute("DELETE FROM sessions")
            if not SESSIONS_DIR.exists():
                return

            for session_file in sorted(SESSIONS_DIR.glob("*.json")):
                try:
                    data = json.loads(session_file.read_text())
                    conn.execute(
                        """INSERT OR REPLACE INTO sessions
                           (transcript_id, date, title, summary,
                            message_count, word_count, human_word_count,
                            source_file, processed_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (data.get("id", session_file.stem),
                         data.get("date", ""),
                         data.get("title", ""),
                         data.get("summary", ""),
                         data.get("message_count", 0),
                         data.get("word_count", 0),
                         data.get("human_word_count", 0),
                         session_file.name,
                         data.get("processed_at", ""))
                    )
                except Exception as e:
                    logger.warning(f"Error indexing {session_file.name}: {e}")

        self._execute(do)

    def _rebuild_observations(self):
        """Re-index observations from JSONL."""
        def do(conn):
            conn.execute("DELETE FROM observations")
            obs_file = DATA_DIR / "observations.jsonl"
            if not obs_file.exists():
                return

            with open(obs_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        conn.execute(
                            """INSERT INTO observations
                               (ts, session_id, tool, file, summary)
                               VALUES (?, ?, ?, ?, ?)""",
                            (entry.get("ts", ""),
                             entry.get("session", ""),
                             entry.get("tool", ""),
                             entry.get("file", ""),
                             entry.get("summary", ""))
                        )
                    except Exception as e:
                        logger.warning(f"Error indexing observation: {e}")

        self._execute(do)

    def _rebuild_prompts(self):
        """Re-index prompts from JSONL."""
        def do(conn):
            conn.execute("DELETE FROM prompts")
            prompts_file = DATA_DIR / "prompts.jsonl"
            if not prompts_file.exists():
                return

            with open(prompts_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        conn.execute(
                            """INSERT INTO prompts
                               (ts, session_id, prompt)
                               VALUES (?, ?, ?)""",
                            (entry.get("ts", ""),
                             entry.get("session", ""),
                             entry.get("prompt", ""))
                        )
                    except Exception as e:
                        logger.warning(f"Error indexing prompt: {e}")

        self._execute(do)

    # ── Session Queries ──────────────────────────────────────

    def search_sessions(self, query: str, limit: int = 10) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT transcript_id, date, title, summary,
                          message_count, word_count, human_word_count
                   FROM sessions
                   WHERE summary LIKE ? OR title LIKE ?
                   ORDER BY date DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_recent_sessions(self, limit: int = 10) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT transcript_id, date, title, summary,
                          message_count, word_count, human_word_count
                   FROM sessions
                   ORDER BY date DESC
                   LIMIT ?""",
                (limit,)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    def get_all_sessions(self, limit: int = 500) -> list[dict]:
        return self.get_recent_sessions(limit=limit)

    # ── Observation Queries ──────────────────────────────────

    def search_observations(self, query: str, limit: int = 20) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT rowid, ts, session_id, tool, file, summary
                   FROM observations
                   WHERE summary LIKE ? OR file LIKE ? OR tool LIKE ?
                   ORDER BY ts DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    # ── Prompt Queries ───────────────────────────────────────

    def search_prompts(self, query: str, limit: int = 20) -> list[dict]:
        def do(conn):
            cur = conn.execute(
                """SELECT rowid, ts, session_id, prompt
                   FROM prompts
                   WHERE prompt LIKE ?
                   ORDER BY ts DESC
                   LIMIT ?""",
                (f"%{query}%", limit)
            )
            return _rows_to_dicts(cur)
        return self._query(do)

    # ── Stats ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        def do(conn):
            def count(sql):
                return conn.execute(sql).fetchone()[0]
            return {
                "sessions": count("SELECT COUNT(*) FROM sessions"),
                "observations": count("SELECT COUNT(*) FROM observations"),
                "prompts": count("SELECT COUNT(*) FROM prompts"),
                "total_words": count("SELECT COALESCE(SUM(word_count), 0) FROM sessions"),
            }
        return self._query(do)

    # ── File-based reads (bypass index) ──────────────────────

    @staticmethod
    def read_session_files(limit: int = 10) -> list[dict]:
        """Read session JSON files directly, sorted by date descending."""
        if not SESSIONS_DIR.exists():
            return []
        sessions = []
        for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
            try:
                sessions.append(json.loads(f.read_text()))
            except Exception:
                continue
            if len(sessions) >= limit:
                break
        return sessions

    @staticmethod
    def get_session_file_count() -> int:
        """Count session files without reading them."""
        if not SESSIONS_DIR.exists():
            return 0
        return len(list(SESSIONS_DIR.glob("*.json")))


def _rows_to_dicts(cursor) -> list[dict]:
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


# ── Schema ────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    transcript_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    message_count INTEGER DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    human_word_count INTEGER DEFAULT 0,
    source_file TEXT DEFAULT '',
    processed_at TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);

CREATE TABLE IF NOT EXISTS observations (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    session_id TEXT,
    tool TEXT,
    file TEXT,
    summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);

CREATE TABLE IF NOT EXISTS prompts (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    session_id TEXT,
    prompt TEXT
);

CREATE INDEX IF NOT EXISTS idx_prompts_ts ON prompts(ts);
CREATE INDEX IF NOT EXISTS idx_prompts_session ON prompts(session_id);
"""
