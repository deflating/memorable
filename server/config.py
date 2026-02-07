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

    # Processing
    "processing_model": "deepseek",
    "deepseek_api_url": "https://api.deepseek.com/chat/completions",
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-chat",

    # Local model (Phase 2)
    "local_model": "ollama",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "llama3.1:8b",

    # Watcher
    "watcher_enabled": True,
    "stale_minutes": 15,
    "min_messages": 15,
    "min_human_words": 100,

    # Rolling summaries
    "summary_days": 5,
    "summary_max_sessions": 20,

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
        # Return config without sensitive keys
        safe = dict(self._data)
        for key in ("deepseek_api_key",):
            if key in safe and safe[key]:
                safe[key] = safe[key][:8] + "..."
        return safe
