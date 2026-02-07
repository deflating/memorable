"""Configuration for Memorable.

Reads from ~/.memorable/config.json with sensible defaults.
"""

import json
from pathlib import Path


DEFAULT_CONFIG_PATH = Path.home() / ".memorable" / "config.json"

DEFAULTS = {
    # Paths
    "memory_dir": str(Path.home() / "claude-memory"),
    "db_path": str(Path.home() / ".memorable" / "memorable.db"),
    "transcript_dirs": [str(Path.home() / ".claude" / "projects")],

    # libSQL sync — set sync_url to enable embedded replica mode
    # When set, db_path becomes the local replica and syncs with the server
    # When unset, db_path is used as a plain local SQLite database
    "sync_url": "",  # e.g. "http://192.168.68.58:8081"
    "sync_auth_token": "",  # JWT or empty for no auth

    # LLMLingua compression
    "compression_rate_storage": 0.50,

    # Watcher
    "watcher_enabled": True,
    "stale_minutes": 15,
    "min_messages": 15,
    "min_human_words": 100,

    # Startup seed
    "seed_session_count": 10,      # session notes in startup seed

    # Context seeds
    "live_capture_interval": 20,  # messages between captures
}


class Config:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self._data = dict(DEFAULTS)
        self._load()

    def _load(self):
        if self.config_path.exists():
            with open(self.config_path) as f:
                user_config = json.load(f)
            self._data.update(user_config)

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    def __getitem__(self, key):
        return self._data[key]

    def as_dict(self) -> dict:
        return dict(self._data)
