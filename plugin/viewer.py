"""Minimal local viewer for Memorable session notes.

Run: python3 viewer.py
Opens: http://localhost:8420
"""

import json
import glob
import html
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import webbrowser

SESSIONS_DIR = Path.home() / ".memorable" / "data" / "sessions"
PORT = 8420


def load_sessions():
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            sessions.append(json.loads(f.read_text()))
        except Exception:
            continue
    sessions.sort(key=lambda s: s.get("date", ""), reverse=True)
    return sessions


def render_summary(summary: str) -> str:
    """Turn a fact sheet into readable HTML."""
    lines = html.escape(summary).split("\n")
    out = []
    for line in lines:
        if line.startswith("OPENING REQUEST:"):
            out.append(f'<h3 class="section">Opening</h3>')
            continue
        if line.startswith("KEY ACTIONS"):
            out.append(f'<h3 class="section">Actions</h3>')
            continue
        if line.startswith("FILES TOUCHED:"):
            out.append(f'<h3 class="section">Files</h3>')
            text = line.replace("FILES TOUCHED:", "").strip()
            files = [f.strip() for f in text.split(",")]
            out.append('<div class="files">' + ", ".join(f'<code>{f}</code>' for f in files) + '</div>')
            continue
        if line.startswith("TOOL USAGE:"):
            out.append(f'<div class="tools">{line}</div>')
            continue
        if line.startswith("ERRORS HIT:"):
            out.append(f'<h3 class="section errors">Errors</h3>')
            continue
        if line.startswith("USER QUOTES"):
            out.append(f'<h3 class="section">Quotes</h3>')
            continue
        if line.startswith("SESSION ENDING:"):
            out.append(f'<h3 class="section">Ending</h3>')
            continue
        if line.startswith("STATS:"):
            out.append(f'<div class="stats">{line}</div>')
            continue
        if line.startswith("- "):
            text = line[2:]
            if text.startswith('"') and text.endswith('"'):
                out.append(f'<div class="quote">{text}</div>')
            elif "→" in text:
                out.append(f'<div class="error-line">{text}</div>')
            else:
                out.append(f'<div class="action">{text}</div>')
            continue
        if line.startswith("User:") or line.startswith("Claude:"):
            speaker = "user" if line.startswith("User:") else "claude"
            out.append(f'<div class="ending-msg {speaker}">{line}</div>')
            continue
        if line.strip():
            out.append(f'<p>{line}</p>')
    return "\n".join(out)


def build_page() -> str:
    sessions = load_sessions()

    # Group by date
    by_date = {}
    for s in sessions:
        d = s.get("date", "unknown")
        by_date.setdefault(d, []).append(s)

    cards = []
    for date in sorted(by_date.keys(), reverse=True):
        day_sessions = by_date[date]
        cards.append(f'<div class="date-header">{date} <span class="count">({len(day_sessions)} sessions)</span></div>')
        for s in day_sessions:
            title = html.escape(s.get("title", "Untitled"))
            summary_html = render_summary(s.get("summary", ""))
            msgs = s.get("message_count", 0)
            words = s.get("word_count", 0)
            cards.append(f'''
            <details class="session">
                <summary class="session-header">
                    <span class="title">{title}</span>
                    <span class="meta">{msgs} msgs &middot; {words:,} words</span>
                </summary>
                <div class="session-body">{summary_html}</div>
            </details>''')

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Memorable</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', system-ui, sans-serif;
           background: #0a0a0a; color: #d4d4d4; max-width: 720px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.4em; color: #e5e5e5; margin-bottom: 4px; }}
    .subtitle {{ color: #737373; font-size: 0.85em; margin-bottom: 32px; }}
    .date-header {{ font-size: 0.9em; font-weight: 600; color: #a3a3a3; padding: 16px 0 8px;
                    border-top: 1px solid #262626; margin-top: 8px; }}
    .date-header:first-child {{ border-top: none; margin-top: 0; }}
    .count {{ font-weight: 400; color: #525252; }}
    .session {{ margin: 4px 0; }}
    .session-header {{ cursor: pointer; padding: 8px 12px; border-radius: 6px; display: flex;
                       justify-content: space-between; align-items: center; list-style: none; }}
    .session-header:hover {{ background: #171717; }}
    .session-header::-webkit-details-marker {{ display: none; }}
    .title {{ color: #e5e5e5; font-size: 0.9em; }}
    .meta {{ color: #525252; font-size: 0.75em; white-space: nowrap; margin-left: 12px; }}
    .session-body {{ padding: 12px 16px 20px; background: #111; border-radius: 0 0 6px 6px;
                     margin: 0 0 4px; font-size: 0.85em; line-height: 1.6; }}
    .section {{ color: #a3a3a3; font-size: 0.8em; font-weight: 600; text-transform: uppercase;
                letter-spacing: 0.05em; margin: 16px 0 6px; }}
    .section:first-child {{ margin-top: 0; }}
    .section.errors {{ color: #ef4444; }}
    .action {{ color: #a3a3a3; padding: 2px 0 2px 12px; border-left: 2px solid #262626; margin: 2px 0; }}
    .quote {{ color: #c4b5fd; padding: 4px 0 4px 12px; border-left: 2px solid #7c3aed; margin: 4px 0;
              font-style: italic; }}
    .error-line {{ color: #fca5a5; padding: 2px 0 2px 12px; border-left: 2px solid #ef4444;
                   margin: 2px 0; font-family: monospace; font-size: 0.85em; }}
    .files {{ margin: 4px 0; }}
    .files code {{ background: #1a1a2e; color: #93c5fd; padding: 2px 6px; border-radius: 3px;
                   font-size: 0.8em; margin: 2px; display: inline-block; }}
    .tools {{ color: #737373; font-size: 0.8em; margin: 8px 0; }}
    .stats {{ color: #525252; font-size: 0.75em; margin-top: 12px; }}
    .ending-msg {{ padding: 2px 0 2px 12px; margin: 2px 0; }}
    .ending-msg.user {{ border-left: 2px solid #525252; color: #a3a3a3; }}
    .ending-msg.claude {{ border-left: 2px solid #3b82f6; color: #93c5fd; }}
    p {{ margin: 4px 0; }}
    details[open] .session-header {{ background: #171717; border-radius: 6px 6px 0 0; }}
</style>
</head><body>
<h1>Memorable</h1>
<div class="subtitle">{len(sessions)} sessions</div>
{''.join(cards)}
</body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(build_page().encode())

    def log_message(self, format, *args):
        pass  # quiet


if __name__ == "__main__":
    print(f"Memorable viewer → http://localhost:{PORT}")
    webbrowser.open(f"http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
