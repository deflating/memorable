"""Entry point for the Memorable MCP server.

Usage:
    python -m server              # Run MCP server (stdio)
    python -m server --watch      # Run with transcript watcher
    python -m server --watcher    # Run watcher only (no MCP server, for launchd)
    python -m server --process    # Process pending transcripts and exit
    python -m server --init       # Initialize config and database
"""

import argparse
import sys
from pathlib import Path

from .config import Config
from .mcp_server import MemorableMCP
from .db import MemorableDB


def main():
    parser = argparse.ArgumentParser(description="Memorable MCP server")
    parser.add_argument("--watch", action="store_true",
                        help="Start transcript file watcher in background thread")
    parser.add_argument("--watcher", action="store_true",
                        help="Run watcher only in foreground (for launchd)")
    parser.add_argument("--process", action="store_true",
                        help="Process pending transcripts and exit")
    parser.add_argument("--init", action="store_true",
                        help="Initialize config and database")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to config file")
    args = parser.parse_args()

    config = Config(Path(args.config)) if args.config else Config()

    if args.init:
        _init(config)
        return

    if args.process:
        _process(config)
        return

    if args.watcher:
        _run_watcher(config)
        return

    # Default: run as MCP server
    server = MemorableMCP(config)

    if args.watch:
        _start_watcher(config)

    server.run()


def _init(config: Config):
    """Initialize Memorable: create config, database, and default data."""
    print("Initializing Memorable...")

    # Create config with defaults
    config.save()
    print(f"  Config: {config.config_path}")

    # Create database
    db = MemorableDB(Path(config["db_path"]))
    print(f"  Database: {config['db_path']}")

    print("Done. Edit ~/.memorable/config.json to configure.")


def _process(config: Config):
    """Process pending transcripts once and exit."""
    from .processor import TranscriptProcessor
    processor = TranscriptProcessor(config)
    processor.process_all()


def _run_watcher(config: Config):
    """Run the watcher in foreground (for launchd daemon)."""
    from .watcher import TranscriptWatcher
    print("Memorable watcher starting...", file=sys.stderr)
    watcher = TranscriptWatcher(config)
    watcher.start()  # blocks


def _start_watcher(config: Config):
    """Start the file watcher in a background thread."""
    from .watcher import TranscriptWatcher
    import threading

    watcher = TranscriptWatcher(config)
    thread = threading.Thread(target=watcher.start, daemon=True)
    thread.start()
    print("Transcript watcher started.", file=sys.stderr)


if __name__ == "__main__":
    main()
