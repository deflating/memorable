"""Entry point for the Memorable MCP server.

Usage:
    python -m server              # Run MCP server (stdio)
    python -m server --watch      # Run with transcript watcher
    python -m server --watcher    # Run watcher only (no MCP server, for launchd)
    python -m server --process    # Process pending transcripts and exit
    python -m server --rebuild    # Rebuild index from files and exit
"""

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from .config import Config
from .mcp_server import MemorableMCP
from .db import MemorableDB, DEFAULT_INDEX_PATH


def _setup_logging():
    """Configure logging to both stderr and file with rotation."""
    log_dir = Path.home() / ".memorable"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "memorable.log"

    logger = logging.getLogger("server")
    logger.setLevel(logging.INFO)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=3
    )
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_formatter = logging.Formatter("%(levelname)s: %(message)s")
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def main():
    _setup_logging()
    logger = logging.getLogger("server")

    parser = argparse.ArgumentParser(description="Memorable MCP server")
    parser.add_argument("--watch", action="store_true",
                        help="Start transcript file watcher in background thread")
    parser.add_argument("--watcher", action="store_true",
                        help="Run watcher only in foreground (for launchd)")
    parser.add_argument("--process", action="store_true",
                        help="Process pending transcripts and exit")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild local index from files and exit")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to config file")
    args = parser.parse_args()

    config = Config(Path(args.config)) if args.config else Config()
    logger.info(f"Starting Memorable with config from {config.config_path}")

    if args.rebuild:
        db = MemorableDB(DEFAULT_INDEX_PATH)
        db.rebuild_from_files()
        stats = db.get_stats()
        print(f"Index rebuilt: {stats['sessions']} sessions, "
              f"{stats['observations']} observations, {stats['prompts']} prompts")
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


def _process(config: Config):
    """Process pending transcripts once and exit."""
    from .processor import TranscriptProcessor
    processor = TranscriptProcessor(config)
    processor.process_all()
    # Rebuild index after processing
    db = MemorableDB(DEFAULT_INDEX_PATH)
    db.rebuild_from_files()


def _run_watcher(config: Config):
    """Run the watcher in foreground (for launchd daemon)."""
    from .watcher import TranscriptWatcher
    print("Memorable watcher starting...", file=sys.stderr)
    watcher = TranscriptWatcher(config)
    watcher.start()


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
