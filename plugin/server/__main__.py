"""Entry point for the Memorable MCP server.

Usage:
    python -m server --mcp        # Run MCP server (stdio)
    python -m server              # Same as --mcp (default)
"""

import logging
import logging.handlers
import sys
from pathlib import Path

from .mcp_server import MemorableMCP


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

    logger.info("Starting Memorable MCP server")

    server = MemorableMCP()
    server.run()


if __name__ == "__main__":
    main()
