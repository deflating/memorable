"""File watcher for new session transcripts.

Uses watchdog to monitor transcript directories for new/changed .jsonl files.
When a file hasn't been modified for stale_minutes, queues it for processing.
"""

import time
import threading
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .config import Config
from .processor import TranscriptProcessor


class TranscriptHandler(FileSystemEventHandler):
    """Handles new/modified .jsonl files."""

    def __init__(self, processor: TranscriptProcessor, stale_minutes: int = 15):
        self.processor = processor
        self.stale_minutes = stale_minutes
        self._pending = {}  # path -> last_modified_time
        self._lock = threading.Lock()

    def on_created(self, event):
        if event.src_path.endswith(".jsonl"):
            self._track(event.src_path)

    def on_modified(self, event):
        if event.src_path.endswith(".jsonl"):
            self._track(event.src_path)

    def _track(self, path: str):
        with self._lock:
            self._pending[path] = time.time()

    def check_stale(self):
        """Check for transcripts that have been idle long enough to process."""
        now = time.time()
        ready = []
        with self._lock:
            for path, last_mod in list(self._pending.items()):
                if (now - last_mod) > (self.stale_minutes * 60):
                    ready.append(path)
                    del self._pending[path]

        if ready:
            # Trigger a full scan+process cycle
            self.processor.process_all()


class TranscriptWatcher:
    """Watches transcript directories and processes new sessions."""

    def __init__(self, config: Config):
        self.config = config
        self.processor = TranscriptProcessor(config)
        self.handler = TranscriptHandler(
            self.processor,
            stale_minutes=config.get("stale_minutes", 15),
        )
        self.observer = Observer()

    def start(self):
        """Start watching. Blocks until stopped."""
        # Watch all transcript directories
        for transcript_dir in self.config["transcript_dirs"]:
            path = Path(transcript_dir)
            if path.exists():
                self.observer.schedule(self.handler, str(path), recursive=True)

        self.observer.start()

        # Also do an initial scan
        self.processor.process_all()

        # Periodic check for stale files
        try:
            while True:
                time.sleep(60)
                self.handler.check_stale()
        except KeyboardInterrupt:
            self.observer.stop()
        self.observer.join()

    def stop(self):
        self.observer.stop()
