"""
Memorable web viewer -- HTTP server for browsing seeds, notes, and anchors.

Uses only Python stdlib. No external dependencies.
"""

import argparse
import json
import shutil
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# -- Paths -----------------------------------------------------------------

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
NOTES_DIR = DATA_DIR / "notes"
ANCHORS_DIR = DATA_DIR / "anchors"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

# -- Salience decay --------------------------------------------------------

DECAY_FACTOR = 0.97
MIN_SALIENCE = 0.05


def effective_salience(entry):
    salience = entry.get("salience", 1.0)
    emotional_weight = entry.get("emotional_weight", 0.3)
    last_ref = entry.get("last_referenced", entry.get("ts", ""))
    try:
        ts_clean = str(last_ref).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except (ValueError, TypeError):
        days = 30
    adjusted_days = days * (1.0 - emotional_weight * 0.5)
    decayed = salience * (DECAY_FACTOR ** adjusted_days)
    return max(MIN_SALIENCE, decayed)


# -- Data loading helpers --------------------------------------------------


def load_config():
    """Load ~/.memorable/config.json, returning {} on any error."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def valid_seed_names():
    """Return the set of valid seed names (without .md extension)."""
    config = load_config()
    user_name = config.get("user_name")
    if user_name:
        return {user_name.lower(), "claude", "now"}
    # Fallback: scan the seeds dir for anything that is not claude.md or now.md
    names = {"claude", "now"}
    if SEEDS_DIR.is_dir():
        for f in SEEDS_DIR.iterdir():
            if f.suffix == ".md" and f.stem.lower() not in ("claude", "now"):
                names.add(f.stem.lower())
    return names


def load_seeds():
    """Return list of seed dicts: name, filename, content."""
    seeds = []
    if not SEEDS_DIR.is_dir():
        return seeds
    for path in sorted(SEEDS_DIR.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            content = ""
        seeds.append({
            "name": path.stem,
            "filename": path.name,
            "content": content,
        })
    return seeds


def load_jsonl_dir(directory):
    """Yield (machine, obj) for every line in every .jsonl file in directory."""
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.jsonl")):
        machine = path.stem  # filename without .jsonl
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                yield machine, obj
            except json.JSONDecodeError:
                continue


def load_all_notes():
    """Return list of note dicts, each enriched with machine + effective_salience."""
    notes = []
    for machine, obj in load_jsonl_dir(NOTES_DIR):
        obj["machine"] = machine
        obj["effective_salience"] = effective_salience(obj)
        notes.append(obj)
    return notes


def load_all_anchors():
    """Return list of anchor dicts, each enriched with machine field."""
    anchors = []
    for machine, obj in load_jsonl_dir(ANCHORS_DIR):
        obj["machine"] = machine
        anchors.append(obj)
    return anchors


# -- API handlers ----------------------------------------------------------


def handle_get_seeds():
    return 200, {"seeds": load_seeds()}


def handle_put_seed(name, body):
    names = valid_seed_names()
    if name not in names:
        return 400, {"error": f"Invalid seed name '{name}'. Valid: {sorted(names)}"}

    content = body.get("content")
    if content is None:
        return 400, {"error": "Missing 'content' field in body"}

    seed_path = SEEDS_DIR / f"{name}.md"

    # Create .bak backup if the file already exists
    if seed_path.exists():
        bak_path = seed_path.with_suffix(".md.bak")
        shutil.copy2(seed_path, bak_path)

    SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(content, encoding="utf-8")
    return 200, {"ok": True}


def handle_get_machines():
    """Return sorted list of unique machine names across notes and anchors."""
    machines = set()
    for machine, _ in load_jsonl_dir(NOTES_DIR):
        machines.add(machine)
    for machine, _ in load_jsonl_dir(ANCHORS_DIR):
        machines.add(machine)
    return 200, {"machines": sorted(machines)}


def handle_get_notes(params):
    search = params.get("search", [""])[0].lower()
    tag_filter = params.get("tag", [""])[0].lower()
    machine_filter = params.get("machine", [""])[0]
    sort_by = params.get("sort", ["date"])[0]
    limit = int(params.get("limit", ["50"])[0])
    offset = int(params.get("offset", ["0"])[0])

    notes = load_all_notes()

    # Filter by machine
    if machine_filter:
        notes = [n for n in notes if n.get("machine") == machine_filter]

    # Filter by search query
    if search:
        filtered = []
        for n in notes:
            text = n.get("note", "")
            tags_str = " ".join(n.get("topic_tags", []))
            haystack = f"{text} {tags_str}".lower()
            if search in haystack:
                filtered.append(n)
        notes = filtered

    # Filter by tag
    if tag_filter:
        notes = [
            n for n in notes
            if tag_filter in [t.lower() for t in n.get("topic_tags", [])]
        ]

    total = len(notes)

    # Sort
    if sort_by == "salience":
        notes.sort(key=lambda n: n.get("effective_salience", 0), reverse=True)
    elif sort_by == "date_asc":
        notes.sort(key=lambda n: n.get("ts", ""))
    else:
        # Default: date descending (newest first)
        notes.sort(key=lambda n: n.get("ts", ""), reverse=True)

    # Paginate
    notes = notes[offset: offset + limit]

    return 200, {"notes": notes, "total": total}


def handle_get_notes_tags():
    notes = load_all_notes()
    tag_counts = {}
    for n in notes:
        for tag in n.get("topic_tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    tags = [{"name": name, "count": count} for name, count in tag_counts.items()]
    tags.sort(key=lambda t: t["count"], reverse=True)
    return 200, {"tags": tags}


def handle_get_anchors(params):
    session_filter = params.get("session", [""])[0]
    machine_filter = params.get("machine", [""])[0]
    anchors = load_all_anchors()

    if machine_filter:
        anchors = [a for a in anchors if a.get("machine") == machine_filter]
    if session_filter:
        anchors = [a for a in anchors if a.get("session") == session_filter]

    return 200, {"anchors": anchors, "total": len(anchors)}


def handle_get_stats():
    notes = load_all_notes()
    anchors = load_all_anchors()
    seeds = load_seeds()

    all_tags = set()
    all_dates = []
    for n in notes:
        for tag in n.get("topic_tags", []):
            all_tags.add(tag)
        ts = n.get("first_ts", n.get("ts", ""))
        if ts:
            all_dates.append(str(ts))

    earliest = min(all_dates) if all_dates else ""
    latest = max(all_dates) if all_dates else ""

    return 200, {
        "note_count": len(notes),
        "anchor_count": len(anchors),
        "seed_count": len(seeds),
        "unique_tags": len(all_tags),
        "date_range": {
            "earliest": earliest,
            "latest": latest,
        },
    }


# -- Request handler -------------------------------------------------------

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json",
}


class MemorableHandler(SimpleHTTPRequestHandler):
    """Routes API requests and serves static files from the ui/ directory."""

    def log_message(self, format, *args):
        # Quieter logging -- suppress default output
        pass

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # -- API routes ----------------------------------------------------
        if path == "/api/seeds":
            status, data = handle_get_seeds()
            return self.send_json(status, data)

        if path == "/api/machines":
            status, data = handle_get_machines()
            return self.send_json(status, data)

        if path == "/api/notes/tags":
            status, data = handle_get_notes_tags()
            return self.send_json(status, data)

        if path == "/api/notes":
            status, data = handle_get_notes(params)
            return self.send_json(status, data)

        if path == "/api/anchors":
            status, data = handle_get_anchors(params)
            return self.send_json(status, data)

        if path == "/api/stats":
            status, data = handle_get_stats()
            return self.send_json(status, data)

        # -- Static files from ui/ -----------------------------------------
        self.serve_static(path)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # PUT /api/seeds/:name
        if path.startswith("/api/seeds/"):
            name = path[len("/api/seeds/"):].strip("/").lower()
            body = self.read_body()
            status, data = handle_put_seed(name, body)
            return self.send_json(status, data)

        self.send_json(404, {"error": "Not found"})

    def serve_static(self, url_path):
        """Serve a file from the UI directory."""
        # Normalise: / -> /index.html
        if url_path == "/" or url_path == "":
            url_path = "/index.html"

        # Security: prevent path traversal
        rel = url_path.lstrip("/")
        file_path = (UI_DIR / rel).resolve()
        if not str(file_path).startswith(str(UI_DIR.resolve())):
            self.send_error(403, "Forbidden")
            return

        if not file_path.is_file():
            # SPA fallback: serve index.html for non-file paths
            index = UI_DIR / "index.html"
            if index.is_file():
                file_path = index
            else:
                self.send_error(404, "Not found")
                return

        ext = file_path.suffix.lower()
        content_type = CONTENT_TYPES.get(ext, "application/octet-stream")

        try:
            data = file_path.read_bytes()
        except Exception:
            self.send_error(500, "Internal server error")
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)


# -- Entry point -----------------------------------------------------------


def run(port=7777):
    server = HTTPServer(("0.0.0.0", port), MemorableHandler)
    print(f"Memorable viewer running at http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Memorable web viewer")
    parser.add_argument("--port", type=int, default=7777, help="Port to listen on (default: 7777)")
    args = parser.parse_args()
    run(port=args.port)
