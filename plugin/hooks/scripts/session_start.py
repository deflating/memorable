#!/usr/bin/env python3
"""SessionStart hook for Memorable.

Reads seed files and recent anchors to inject startup context.

Two layers:
1. Seeds — {user_name}.md + claude.md (from ~/.memorable/data/seeds/)
2. Anchors — recent in-session summaries (from ~/.memorable/data/anchors/*.jsonl)
"""

import json
import socket
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"
SEEDS_DIR = DATA_DIR / "seeds"
ANCHORS_DIR = DATA_DIR / "anchors"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"

# Rough token budget: seeds get priority, anchors fill the rest
MAX_SEED_CHARS = 8000
MAX_ANCHOR_CHARS = 6000


def _get_config() -> dict:
    """Read config file."""
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


def _get_machine_id() -> str:
    """Read machine_id from config, fall back to hostname."""
    mid = _get_config().get("machine_id")
    return mid if mid else socket.gethostname()


def _get_user_name() -> str:
    """Read user_name from config, fall back to detecting from seed files."""
    name = _get_config().get("user_name")
    if name:
        return name
    # Fall back: look for any .md that isn't claude.md
    if SEEDS_DIR.exists():
        for path in sorted(SEEDS_DIR.glob("*.md")):
            if path.stem != "claude":
                return path.stem
    return ""


def _read_seeds() -> str:
    """Read seed files: user file first, then claude.md."""
    if not SEEDS_DIR.exists():
        return ""

    parts = []
    user_name = _get_user_name()

    # Read user seed file first
    if user_name:
        user_path = SEEDS_DIR / f"{user_name}.md"
        if user_path.exists():
            content = user_path.read_text().strip()
            if content:
                parts.append(content)

    # Then claude.md
    claude_path = SEEDS_DIR / "claude.md"
    if claude_path.exists():
        content = claude_path.read_text().strip()
        if content:
            parts.append(content)

    # Any other seed files (future-proofing), excluding now.md (read separately after anchors)
    if SEEDS_DIR.exists():
        for path in sorted(SEEDS_DIR.glob("*.md")):
            if path.stem not in [user_name, "claude", "now"]:
                content = path.read_text().strip()
                if content:
                    parts.append(content)

    result = "\n\n".join(parts)
    if len(result) > MAX_SEED_CHARS:
        result = result[:MAX_SEED_CHARS] + "\n...(truncated)"
    return result


def _read_anchors(days: int = 5) -> str:
    """Read recent anchor entries from the last N days, grouped by day."""
    if not ANCHORS_DIR.exists():
        return ""

    cutoff = datetime.now() - timedelta(days=days)
    entries = []

    # Read all anchor JSONL files (from any machine)
    for anchor_file in ANCHORS_DIR.glob("*.jsonl"):
        try:
            with open(anchor_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("ts", "")
                        if ts:
                            entry_dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("Z", ""))
                            if entry_dt.replace(tzinfo=None) >= cutoff:
                                entries.append(entry)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            continue

    if not entries:
        return ""

    # Sort by timestamp descending
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)

    # Group by day
    by_day: dict[str, list[dict]] = {}
    for entry in entries:
        ts = entry.get("ts", "")
        day = ts[:10] if len(ts) >= 10 else "unknown"
        by_day.setdefault(day, []).append(entry)

    parts = ["## Recent Activity"]
    total_chars = 0
    for day in sorted(by_day.keys(), reverse=True):
        day_entries = by_day[day]
        try:
            day_label = datetime.strptime(day, "%Y-%m-%d").strftime("%b %d, %Y")
        except ValueError:
            day_label = day
        day_section = f"\n### {day_label}"
        for entry in day_entries:
            summary = entry.get("summary", "")
            mood = entry.get("mood", "")
            open_threads = entry.get("open_threads", [])

            line = f"- {summary}"
            if mood:
                line = f"- [{mood}] {summary}"
            if open_threads:
                line += f" (threads: {', '.join(open_threads)})"

            ts = entry.get("ts", "")
            if len(ts) >= 16:
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    line += f" — {t.strftime('%I:%M %p')}"
                except ValueError:
                    pass

            day_section += f"\n{line}"

        if total_chars + len(day_section) > MAX_ANCHOR_CHARS:
            break
        parts.append(day_section)
        total_chars += len(day_section)

    return "\n".join(parts)


def _read_now() -> str:
    """Read now.md — the rolling current-state snapshot."""
    now_path = SEEDS_DIR / "now.md"
    if now_path.exists():
        content = now_path.read_text().strip()
        if content:
            return content
    return ""


def main():
    error_log_path = Path.home() / ".memorable" / "hook-errors.log"

    try:
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

        parts = []

        # ── Layer 1: Seed files ────────────────────────────────
        try:
            seeds = _read_seeds()
            if seeds:
                parts.append("[Memorable]\n")
                parts.append(seeds)
        except Exception as e:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: seeds error: {e}\n")

        # ── Layer 2: Recent anchors ────────────────────────────
        try:
            anchors = _read_anchors(days=5)
            if anchors:
                parts.append("")
                parts.append(anchors)
        except Exception as e:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: anchors error: {e}\n")

        # ── Layer 3: now.md (current state snapshot) ──────────
        try:
            now = _read_now()
            if now:
                parts.append("")
                parts.append(now)
        except Exception as e:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: now error: {e}\n")

        if parts:
            print("\n".join(parts))
        else:
            print("[Memorable] No seed files or anchors found. Use memorable_onboard to set up your identity.")

    except Exception as e:
        try:
            with open(error_log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] session_start: CRITICAL ERROR: {e}\n")
            print(f"[Memorable] Error loading context (logged to {error_log_path})")
        except:
            print("[Memorable] Error loading context")


if __name__ == "__main__":
    main()
