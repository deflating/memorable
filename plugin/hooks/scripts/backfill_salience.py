#!/usr/bin/env python3
"""Backfill existing session notes with salience metadata (topic_tags + emotional_weight).

Reads each note's markdown text, sends a lightweight prompt to DeepSeek
to extract tags and emotional weight, then writes the metadata back.

Skips entries that already have topic_tags set.

Usage:
    python3 backfill_salience.py [--dry-run]
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".memorable" / "data"
CONFIG_PATH = Path.home() / ".memorable" / "config.json"

EXTRACT_PROMPT = """Given this session note, output ONLY a single JSON object (no markdown, no explanation):

{{"topic_tags": ["tag1", "tag2", ...], "emotional_weight": 0.5}}

topic_tags: 3-5 short lowercase tags for the main topics (e.g. "memorable", "daemon", "mac-mini", "job-resignation", "buddy"). Be consistent — use the same tag for the same topic across notes.
emotional_weight: float 0.0-1.0. Use 0.1-0.3 for routine technical work, 0.4-0.6 for meaningful decisions or progress, 0.7-1.0 for strong emotion, major life events, breakthroughs, or significant frustration.

Session note:
{note}"""


def call_deepseek(prompt: str, api_key: str) -> str:
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 200,
    }).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def parse_response(text: str) -> tuple[list[str], float]:
    """Parse the JSON response from DeepSeek."""
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    meta = json.loads(text)
    tags = meta.get("topic_tags", [])
    weight = float(meta.get("emotional_weight", 0.3))
    weight = max(0.0, min(1.0, weight))
    return tags, weight


def main():
    dry_run = "--dry-run" in sys.argv

    cfg = json.loads(CONFIG_PATH.read_text())
    api_key = cfg.get("summarizer", {}).get("api_key", "")
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("No DeepSeek API key found")
        sys.exit(1)

    notes_dir = DATA_DIR / "notes"
    for jsonl_file in sorted(notes_dir.glob("*.jsonl")):
        if jsonl_file.name.endswith(".bak"):
            continue

        print(f"\nProcessing {jsonl_file.name}...")

        # Read all entries
        entries = []
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    entries.append(line)  # preserve unparseable lines as-is

        needs_backfill = 0
        already_done = 0
        errors = 0

        for i, entry in enumerate(entries):
            if isinstance(entry, str):
                continue  # unparseable line

            # Skip if already has tags
            if entry.get("topic_tags"):
                already_done += 1
                continue

            note = entry.get("note", "")
            if not note or len(note) < 20:
                continue

            needs_backfill += 1
            session = entry.get("session", "?")[:8]
            print(f"  [{i+1}/{len(entries)}] session={session}...", end=" ", flush=True)

            if dry_run:
                print("(dry run, skipping)")
                continue

            try:
                prompt = EXTRACT_PROMPT.format(note=note[:3000])
                response = call_deepseek(prompt, api_key)
                tags, weight = parse_response(response)

                entry["topic_tags"] = tags
                entry["emotional_weight"] = weight
                entry["salience"] = entry.get("salience", 1.0)
                entry["last_referenced"] = entry.get("last_referenced", entry.get("ts", ""))
                entry["reference_count"] = entry.get("reference_count", 0)

                print(f"tags={tags} ew={weight:.1f}")

                # Rate limit: DeepSeek free tier
                time.sleep(0.3)

            except Exception as e:
                print(f"ERROR: {e}")
                errors += 1
                continue

        print(f"\n  Summary: {already_done} already done, {needs_backfill} backfilled, {errors} errors")

        if not dry_run and needs_backfill > 0:
            # Write back
            with open(jsonl_file, "w") as f:
                for entry in entries:
                    if isinstance(entry, str):
                        f.write(entry + "\n")
                    else:
                        f.write(json.dumps(entry) + "\n")
            print(f"  Written to {jsonl_file}")


if __name__ == "__main__":
    main()
