#!/usr/bin/env python3
"""Tiny server for the Memorable viewer. Serves HTML + JSONL data."""

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"
VIEWER = Path(__file__).parent / "viewer.html"
PORT = 8420


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            self.send_data()
        elif self.path == "/" or self.path == "/index.html":
            self.send_file(VIEWER, "text/html")
        else:
            self.send_error(404)

    def send_data(self):
        entries = []
        for subdir, _type in [("observations", "observation"), ("prompts", "prompt"), ("anchors", "anchor"), ("summaries", "summary")]:
            d = DATA_DIR / subdir
            if not d.exists():
                continue
            for f in d.glob("*.jsonl"):
                machine = f.stem  # e.g. "Matts-MacBook-Pro-4.local"
                for line in f.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        obj["_type"] = _type
                        obj["_machine"] = obj.get("machine", machine)
                        entries.append(obj)
                    except json.JSONDecodeError:
                        pass

        # Also check legacy flat files
        for name, _type in [("observations.jsonl", "observation"), ("prompts.jsonl", "prompt")]:
            f = DATA_DIR / name
            if not f.exists():
                continue
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    obj["_type"] = _type
                    obj["_machine"] = obj.get("machine", "unknown")
                    entries.append(obj)
                except json.JSONDecodeError:
                    pass

        body = json.dumps(entries).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # quiet


if __name__ == "__main__":
    print(f"Memorable viewer: http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
