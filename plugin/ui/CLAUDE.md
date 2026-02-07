# Memorable Web Viewer

A dark-themed dashboard for browsing Memorable's data: sessions, observations, user prompts, and the knowledge graph.

## Architecture

```
ui/
├── viewer.html       # HTML template — just structure, no inline code
├── viewer.css        # All styles — design tokens, components, responsive
└── js/
    ├── app.js        # Entry point: state, routing, data loading, events
    ├── utils.js      # Shared helpers: esc(), formatTime(), formatDate(), truncate()
    ├── components.js # Reusable card renderers: observations, prompts, sessions
    ├── timeline.js   # Timeline tab: chronological list with date grouping
    ├── sessions.js   # Session detail view (click into a session)
    ├── search.js     # Search results rendering
    └── kg.js         # Knowledge graph: force-directed canvas visualization
```

All JS files use ES modules (`import`/`export`). The entry point is `app.js`, loaded via `<script type="module">`.

## Backend API

Served by `server/web.py` on port 7777. Endpoints:

- `GET /` — viewer HTML
- `GET /viewer.css`, `GET /js/*.js` — static assets
- `GET /api/stats` — session count, word count, observation count
- `GET /api/timeline?limit=N` — chronological observations + prompts
- `GET /api/sessions?limit=N` — session list
- `GET /api/session?id=TRANSCRIPT_ID` — single session with its observations and prompts
- `GET /api/search?q=QUERY&limit=N` — search observations and prompts
- `GET /api/kg?min_priority=N` — knowledge graph nodes and edges

All API responses are JSON. The backend reads from a SQLite database.

## Running

```bash
cd /Users/claude/memorable/plugin
python3 -m server.web --port 7777
# Open http://127.0.0.1:7777
```

## Design System

- **Theme**: Dark, gold accent (`#f0c000`)
- **Colors**: CSS custom properties in `:root` — see `viewer.css`
- **Fonts**: System fonts (SF Pro, Inter) for UI, monospace for code/files
- **Radius**: `--radius-sm` (6px), `--radius` (10px), `--radius-lg` (14px)
- **Animations**: fadeIn, slideDown, shimmer (skeletons), pulse

## Current Features

- **Timeline**: Chronological observations + prompts, grouped by date
- **Sessions**: List view + click-through detail with activity
- **Knowledge Graph**: Interactive force-directed graph with pan/zoom/drag/hover
- **Search**: Debounced keyword search (Cmd+K shortcut, Esc to clear)
- **Stats bar**: Session/word/observation/prompt counts
- **Responsive**: Mobile-friendly layout

## Guidelines for Contributors

- Keep the single-page app architecture — no build tools, no npm, no frameworks
- Use ES modules for JS (import/export)
- All styles go in `viewer.css` — no inline styles except animation-delay
- HTML structure stays in `viewer.html` — keep it minimal
- The backend API is stable — add new endpoints in `server/web.py` if you need new data
- Test by running the web server and checking in a browser
- The viewer should work on Safari and Chrome (macOS primarily)
