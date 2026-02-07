# Memorable

Persistent memory for Claude Code sessions. Captures everything you do — tool calls, conversations, decisions — compresses it, extracts a knowledge graph, and makes it all searchable across sessions.

Runs entirely on-device using Apple ML frameworks. No cloud APIs required (except one optional Sonnet call for KG quality filtering).

## What It Does

- **Captures** every tool call and user message during Claude Code sessions
- **Compresses** transcripts to 50% with LLMLingua-2 (still searchable)
- **Generates** session summaries and emoji-tagged headers via Apple's on-device Foundation Model
- **Extracts** a knowledge graph of people, projects, technologies, and their relationships
- **Embeds** everything with Apple NLEmbedding for semantic search
- **Seeds** each new session with relevant context from past sessions

## Requirements

- macOS (Apple Silicon — uses NaturalLanguage.framework, Core ML)
- Python 3.10+
- Claude Code CLI (`claude`)
- Apple Foundation Model CLI (`afm`)
- PyObjC (auto-installed)

## Setup

### 1. Initialize

```bash
cd memorable/plugin
python3 -m server --init
```

Creates `~/.memorable/config.json` and initializes the database at `~/.memorable/memorable.db`.

### 2. Install Dependencies

```bash
pip install -e .
```

Installs: `llmlingua`, `watchdog`, `libsql-experimental`.

### 3. Install as Claude Code Plugin

The plugin registers itself via `.mcp.json` and `hooks/hooks.json`. To install in Claude Code:

```bash
claude plugin install /path/to/memorable/plugin
```

Or add to your MCP config manually. The MCP server runs `python3 -m server --watch` which starts both the MCP JSON-RPC handler and the background file watcher.

### 4. (Optional) Start the Web Viewer

```bash
cd memorable/plugin
python3 -m server.web --port 7777
```

Open http://localhost:7777 to browse sessions, observations, and the knowledge graph.

## How It Works

### During a Session

1. **SessionStart** hook loads context from past sessions (sacred facts, recent summaries)
2. **PostToolUse** hook captures each tool call → queued for async processing
3. **UserPromptSubmit** hook captures and embeds every user message
4. **Stop** hook generates a session-level summary from all observations

### Background Processing (every 30 seconds)

1. Observation queue → deterministic descriptions + 512-dim embeddings
2. KG extraction: Apple AFM proposes entity candidates → NLTagger catches names → Sonnet filters garbage → approved entities stored

### After Session Ends (when transcript idle 15+ minutes)

1. JSONL transcript parsed and validated
2. LLMLingua-2 compresses at 50%
3. Apple AFM generates emoji header tags and session note
4. Everything stored in SQLite

## MCP Tools

| Tool | Description |
|------|-------------|
| `memorable_get_startup_seed` | Load context at session start |
| `memorable_search_sessions` | Keyword search past sessions |
| `memorable_search_observations` | Hybrid semantic + keyword search |
| `memorable_get_observations` | List observations for a session |
| `memorable_record_significant` | Save important moments to KG |
| `memorable_query_kg` | Query the knowledge graph |
| `memorable_get_system_status` | System stats and health |

## Configuration

Edit `~/.memorable/config.json`:

```json
{
  "db_path": "~/.memorable/memorable.db",
  "transcript_dirs": ["~/.claude/projects"],
  "stale_minutes": 15,
  "min_messages": 15,
  "min_human_words": 100,
  "observer_process_interval": 30,
  "compression_rate_storage": 0.50
}
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full configuration reference, database schema, pipeline details, and component documentation.

## CLI

```bash
python3 -m server              # MCP server only (stdio)
python3 -m server --watch      # MCP server + background watcher (normal operation)
python3 -m server --watcher    # Standalone watcher daemon (for launchd)
python3 -m server --process    # Process all pending transcripts and exit
python3 -m server --init       # Initialize config and database
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for comprehensive documentation including:

- Complete data flow diagrams
- Database schema with all tables and columns
- Pipeline details (transcript processing, observation generation, KG extraction)
- MCP tool specifications
- Web viewer API endpoints
- Technology stack and dependencies
- Future plans (MLX fine-tuned model)

## License

MIT
