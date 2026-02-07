"""Lightweight web viewer for Memorable.

Serves a single HTML page + JSON API endpoints.
Run: python3 -m server.web [--port 7777]
"""

import json
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .db import MemorableDB
from .config import Config
from .embeddings import (
    embed_text, cosine_distance, cosine_distance_vectors, semantic_score,
    embedding_available,
)

config = Config()
db = MemorableDB(
    Path(config["db_path"]),
    sync_url=config.get("sync_url", ""),
    auth_token=config.get("sync_auth_token", ""),
)

UI_DIR = Path(__file__).parent.parent / "ui"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _session_body_text(session: dict) -> str:
    """Extract useful comparison text from a session, stripping YAML frontmatter."""
    title = session.get("title", "")
    # Try compressed_50 (Haiku summary) or keyword summary
    body = session.get("compressed_50", "") or session.get("summary", "")
    if body.startswith("---"):
        # Find the closing --- and take everything after
        parts = body.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
    return f"{title}. {body[:400]}"


class MemorableHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        routes = {
            "/": self._serve_viewer,
            "/api/stats": self._api_stats,
            "/api/sessions": self._api_sessions,
            "/api/session": self._api_session_detail,
            "/api/session/related": self._api_session_related,
            "/api/timeline": self._api_timeline,
            "/api/observations": self._api_observations,
            "/api/prompts": self._api_prompts,
            "/api/search": self._api_search,
            "/api/search/semantic": self._api_search_semantic,
            "/api/kg": self._api_kg,
            "/api/analytics/activity": self._api_analytics_activity,
            "/api/analytics/entities": self._api_analytics_entities,
        }

        handler = routes.get(path)
        if handler:
            handler(params)
        elif self._serve_static(path):
            pass  # served
        else:
            self._json_response({"error": "not found"}, 404)

    def _serve_viewer(self, params):
        self._serve_file(UI_DIR / "viewer.html")

    def _serve_static(self, path):
        """Serve static files from ui/ directory. Returns True if served."""
        # Prevent path traversal
        clean = path.lstrip("/")
        if ".." in clean:
            return False
        filepath = UI_DIR / clean
        if filepath.is_file() and UI_DIR in filepath.resolve().parents:
            self._serve_file(filepath)
            return True
        return False

    def _serve_file(self, filepath):
        if not filepath.exists():
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return
        content_type = MIME_TYPES.get(filepath.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(filepath.read_bytes())

    def _api_stats(self, params):
        stats = db.get_stats()
        self._json_response(stats)

    def _api_sessions(self, params):
        limit = int(params.get("limit", [30])[0])
        offset = int(params.get("offset", [0])[0])
        query = params.get("q", [None])[0]
        if query:
            sessions = db.search_sessions(query, limit=limit)
            self._json_response(sessions)
        else:
            items, total = db.get_sessions_paginated(limit=limit, offset=offset)
            self._json_response({
                "items": items, "total": total,
                "offset": offset, "limit": limit,
            })

    def _api_session_detail(self, params):
        tid = params.get("id", [None])[0]
        if not tid:
            self._json_response({"error": "missing ?id= parameter"}, 400)
            return
        session = db.get_session_by_transcript_id(tid)
        if not session:
            self._json_response({"error": "session not found"}, 404)
            return
        obs = db.get_observations_by_session(tid, limit=200)
        prompts = db.get_user_prompts_by_session(tid)
        self._json_response({
            "session": session,
            "observations": obs,
            "prompts": prompts,
        })

    def _api_timeline(self, params):
        limit = int(params.get("limit", [100])[0])
        offset = int(params.get("offset", [0])[0])
        items, total = db.get_timeline_paginated(limit=limit, offset=offset)
        self._json_response({
            "items": items, "total": total,
            "offset": offset, "limit": limit,
        })

    def _api_observations(self, params):
        limit = int(params.get("limit", [50])[0])
        offset = int(params.get("offset", [0])[0])
        session_id = params.get("session_id", [None])[0]
        items, total = db.get_observations_paginated(
            limit=limit, offset=offset, session_id=session_id,
        )
        self._json_response({
            "items": items, "total": total,
            "offset": offset, "limit": limit,
        })

    def _api_prompts(self, params):
        limit = int(params.get("limit", [50])[0])
        session_id = params.get("session_id", [None])[0]
        query = params.get("q", [None])[0]
        if session_id:
            prompts = db.get_user_prompts_by_session(session_id)
        elif query:
            prompts = db.search_user_prompts(query, limit=limit)
        else:
            prompts = db.search_user_prompts("", limit=limit)
        self._json_response(prompts)

    def _api_search(self, params):
        query = params.get("q", [""])[0]
        limit = int(params.get("limit", [20])[0])
        if not query:
            self._json_response({"error": "missing ?q= parameter"}, 400)
            return

        results = []

        # Search observations
        obs = db.search_observations_keyword(query, limit=limit)
        for o in obs:
            results.append({
                "kind": "observation",
                "id": o["id"],
                "type": o.get("observation_type", ""),
                "title": o.get("title", ""),
                "summary": o.get("summary", ""),
                "files": o.get("files", "[]"),
                "session_id": o.get("session_id", ""),
                "created_at": o.get("created_at"),
            })

        # Search prompts
        prompts = db.search_user_prompts(query, limit=limit)
        for p in prompts:
            results.append({
                "kind": "prompt",
                "id": p["id"],
                "type": "prompt",
                "title": p["prompt_text"][:80],
                "summary": p["prompt_text"],
                "session_id": p.get("session_id", ""),
                "created_at": p.get("created_at"),
            })

        # Sort by created_at descending
        results.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
        self._json_response(results[:limit])

    def _api_kg(self, params):
        min_priority = int(params.get("min_priority", [0])[0])
        graph = db.get_kg_graph(min_priority=min_priority)
        self._json_response(graph)

    # ── Analytics endpoints ──────────────────────────────────

    def _api_analytics_activity(self, params):
        """GET /api/analytics/activity?days=90"""
        days = int(params.get("days", [90])[0])

        daily = db.get_daily_activity(days=days)
        hourly = db.get_hourly_distribution()
        by_day_of_week = db.get_day_of_week_distribution()
        by_type = db.get_observation_type_counts()
        totals = db.get_analytics_totals()

        self._json_response({
            "daily": daily,
            "hourly": hourly,
            "by_day_of_week": by_day_of_week,
            "by_type": by_type,
            "totals": totals,
        })

    def _api_analytics_entities(self, params):
        """GET /api/analytics/entities?limit=20"""
        limit = int(params.get("limit", [20])[0])

        top_entities = db.get_top_entities(limit=limit)
        entity_types = db.get_entity_type_counts()
        recent_entities = db.get_recent_entities(limit=limit)

        self._json_response({
            "top_entities": top_entities,
            "entity_types": entity_types,
            "recent_entities": recent_entities,
        })

    # ── Semantic search endpoint ─────────────────────────────

    def _api_search_semantic(self, params):
        """GET /api/search/semantic?q=QUERY&limit=20

        Hybrid keyword + semantic search across observations,
        prompts, and sessions. Same approach as the MCP tools.
        """
        query = params.get("q", [""])[0]
        limit = int(params.get("limit", [20])[0])
        if not query:
            self._json_response({"error": "missing ?q= parameter"}, 400)
            return

        use_semantic = embedding_available()
        query_embedding = embed_text(query) if use_semantic else None

        obs_results = []
        prompt_results = []
        session_results = []

        # ── Observations ──
        keyword_obs = db.search_observations_keyword(query, limit=limit * 2)
        keyword_obs_ids = {o["id"] for o in keyword_obs}

        if query_embedding:
            all_obs = db.get_all_observation_embeddings(limit=5000)
            for obs in all_obs:
                emb = obs.pop("embedding", None)
                is_keyword = obs["id"] in keyword_obs_ids
                if emb:
                    dist = cosine_distance_vectors(query_embedding, emb)
                    sem = semantic_score(dist)
                else:
                    sem = 0.0

                if is_keyword:
                    score = 0.6 * sem + 0.4
                elif sem > 0.15:
                    score = sem
                else:
                    continue

                obs_results.append({
                    "id": obs["id"],
                    "title": obs.get("title", ""),
                    "summary": obs.get("summary", ""),
                    "type": obs.get("observation_type", ""),
                    "session_id": obs.get("session_id", ""),
                    "score": round(score, 3),
                    "created_at": obs.get("created_at"),
                })
        else:
            # Fallback: keyword only
            for obs in keyword_obs:
                obs_results.append({
                    "id": obs["id"],
                    "title": obs.get("title", ""),
                    "summary": obs.get("summary", ""),
                    "type": obs.get("observation_type", ""),
                    "session_id": obs.get("session_id", ""),
                    "score": 0.5,
                    "created_at": obs.get("created_at"),
                })

        # ── User Prompts ──
        keyword_prompts = db.search_user_prompts(query, limit=limit * 2)
        keyword_prompt_ids = {p["id"] for p in keyword_prompts}

        if query_embedding:
            all_prompts = db.get_all_prompt_embeddings(limit=5000)
            for p in all_prompts:
                emb = p.pop("embedding", None)
                is_keyword = p["id"] in keyword_prompt_ids
                if emb:
                    dist = cosine_distance_vectors(query_embedding, emb)
                    sem = semantic_score(dist)
                else:
                    sem = 0.0

                if is_keyword:
                    score = 0.6 * sem + 0.4
                elif sem > 0.15:
                    score = sem
                else:
                    continue

                prompt_results.append({
                    "id": p["id"],
                    "text": p.get("prompt_text", ""),
                    "session_id": p.get("session_id", ""),
                    "score": round(score, 3),
                    "created_at": p.get("created_at"),
                })
        else:
            for p in keyword_prompts:
                prompt_results.append({
                    "id": p["id"],
                    "text": p.get("prompt_text", ""),
                    "session_id": p.get("session_id", ""),
                    "score": 0.5,
                    "created_at": p.get("created_at"),
                })

        # ── Sessions ──
        keyword_sessions = db.search_sessions(query, limit=limit * 2)
        keyword_session_ids = {s["id"] for s in keyword_sessions}

        all_sessions = db.get_all_session_texts(limit=500)
        for s in all_sessions:
            is_keyword = s["id"] in keyword_session_ids
            if use_semantic:
                text = f"{s['title']}. {s.get('summary', '')} {s.get('header', '')}"
                dist = cosine_distance(query, text)
                sem = semantic_score(dist)
            else:
                sem = 0.0

            if is_keyword:
                score = 0.6 * sem + 0.4 if use_semantic else 0.5
            elif sem > 0.15:
                score = sem
            else:
                continue

            session_results.append({
                "transcript_id": s.get("transcript_id", ""),
                "title": s.get("title", ""),
                "header": s.get("header", ""),
                "date": s.get("date", ""),
                "score": round(score, 3),
            })

        # Sort each by score descending and truncate
        obs_results.sort(key=lambda x: x["score"], reverse=True)
        prompt_results.sort(key=lambda x: x["score"], reverse=True)
        session_results.sort(key=lambda x: x["score"], reverse=True)

        self._json_response({
            "observations": obs_results[:limit],
            "prompts": prompt_results[:limit],
            "sessions": session_results[:limit],
        })

    # ── Related sessions endpoint ────────────────────────────

    def _api_session_related(self, params):
        """GET /api/session/related?id=TRANSCRIPT_ID&limit=5

        Find sessions related to a given session by:
        1. Observation embedding similarity (when available)
        2. Session text semantic similarity (fallback)
        3. Shared KG entities (bonus)
        """
        tid = params.get("id", [None])[0]
        limit = int(params.get("limit", [5])[0])
        if not tid:
            self._json_response({"error": "missing ?id= parameter"}, 400)
            return

        session = db.get_session_by_transcript_id(tid)
        if not session:
            self._json_response({"error": "session not found"}, 404)
            return

        target_entities = set(db.get_session_entity_names(tid))

        # Try observation embeddings first, fall back to text similarity
        target_obs = db.get_session_observation_embeddings(tid)
        target_embeddings = [o["embedding"] for o in target_obs if o.get("embedding")]
        use_text_fallback = not target_embeddings and embedding_available()

        # For text fallback, build query text from the session title + body
        target_text = ""
        if use_text_fallback:
            target_text = _session_body_text(session)

        # Score all other sessions
        all_sessions = db.get_all_session_texts(limit=500)
        scored = []

        for s in all_sessions:
            if s.get("transcript_id") == tid:
                continue

            other_tid = s.get("transcript_id", "")
            sim_score = 0.0

            if target_embeddings and embedding_available():
                # Observation embedding similarity
                other_obs = db.get_session_observation_embeddings(other_tid)
                other_embeddings = [o["embedding"] for o in other_obs if o.get("embedding")]

                if other_embeddings:
                    max_sims = []
                    for t_emb in target_embeddings:
                        best = max(
                            semantic_score(cosine_distance_vectors(t_emb, o_emb))
                            for o_emb in other_embeddings
                        )
                        max_sims.append(best)
                    sim_score = sum(max_sims) / len(max_sims)
            elif use_text_fallback:
                # Text similarity: compare title + body text
                other_text = f"{s.get('title', '')}. {s.get('summary', '')[:200]}"
                dist = cosine_distance(target_text, other_text)
                sim_score = semantic_score(dist)

            # Shared entities bonus
            other_entities = set(db.get_session_entity_names(other_tid))
            shared = target_entities & other_entities
            entity_bonus = min(len(shared) * 0.05, 0.3)

            total = min(sim_score + entity_bonus, 1.0)
            if total > 0.1:
                scored.append({
                    "transcript_id": other_tid,
                    "title": s.get("title", ""),
                    "date": s.get("date", ""),
                    "header": s.get("header", ""),
                    "similarity": round(total, 3),
                    "shared_entities": sorted(shared),
                })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        self._json_response(scored[:limit])

    def _json_response(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Quieter logging
        pass


def main():
    parser = argparse.ArgumentParser(description="Memorable web viewer")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), MemorableHandler)
    print(f"Memorable viewer: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
