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

config = Config()
db = MemorableDB(
    Path(config["db_path"]),
    sync_url=config.get("sync_url", ""),
    auth_token=config.get("sync_auth_token", ""),
)

VIEWER_HTML = Path(__file__).parent.parent / "ui" / "viewer.html"


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
            "/api/observations": self._api_observations,
            "/api/prompts": self._api_prompts,
            "/api/search": self._api_search,
        }

        handler = routes.get(path)
        if handler:
            handler(params)
        else:
            self._json_response({"error": "not found"}, 404)

    def _serve_viewer(self, params):
        if not VIEWER_HTML.exists():
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"viewer.html not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(VIEWER_HTML.read_bytes())

    def _api_stats(self, params):
        stats = db.get_stats()
        self._json_response(stats)

    def _api_sessions(self, params):
        limit = int(params.get("limit", [30])[0])
        query = params.get("q", [None])[0]
        if query:
            sessions = db.search_sessions(query, limit=limit)
        else:
            sessions = db.get_recent_summaries(limit=limit)
        self._json_response(sessions)

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

    def _api_observations(self, params):
        limit = int(params.get("limit", [50])[0])
        session_id = params.get("session_id", [None])[0]
        if session_id:
            obs = db.get_observations_by_session(session_id, limit=limit)
        else:
            obs = db.get_recent_observations(limit=limit)
        self._json_response(obs)

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
