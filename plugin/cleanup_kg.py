"""Retroactive KG cleanup — runs all existing entities through Sonnet filter.

Entities with priority >= 7 (manually set or sacred) are kept without filtering.
Everything else goes through Sonnet for validation.

Usage:
    cd memorable/plugin
    python3 cleanup_kg.py              # dry run (shows what would be deleted)
    python3 cleanup_kg.py --apply      # actually delete garbage
"""

import argparse
import json
from pathlib import Path

from server.db import MemorableDB
from server.config import Config
from server.llm import call_llm_json


def main():
    parser = argparse.ArgumentParser(description="Clean up KG entities via Sonnet filter")
    parser.add_argument("--apply", action="store_true", help="Actually delete garbage (default: dry run)")
    parser.add_argument("--min-priority", type=int, default=7,
                        help="Skip entities at or above this priority (default: 7)")
    args = parser.parse_args()

    config = Config()
    db = MemorableDB(
        Path(config["db_path"]),
        sync_url=config.get("sync_url", ""),
        auth_token=config.get("sync_auth_token", ""),
    )

    all_entities = db.get_all_entities(limit=5000)
    print(f"Total entities: {len(all_entities)}")

    # Split into trusted (high priority) and candidates (need filtering)
    trusted = [e for e in all_entities if e.get("priority", 0) >= args.min_priority]
    candidates = [e for e in all_entities if e.get("priority", 0) < args.min_priority]

    print(f"Trusted (priority >= {args.min_priority}): {len(trusted)}")
    print(f"Candidates to filter: {len(candidates)}")

    if not candidates:
        print("Nothing to clean up.")
        return

    # Show trusted entities
    print(f"\n--- KEEPING (trusted) ---")
    for e in trusted:
        print(f"  [{e['priority']}] {e['name']} ({e['type']})")

    # Batch candidates through Sonnet (same logic as kg.py sonnet_filter_entities)
    # Process in batches of 30 to avoid prompt size issues
    batch_size = 30
    all_keep = set()
    all_reject = []

    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        names_list = "\n".join(
            f'- "{c["name"]}" ({c["type"]})'
            for c in batch
        )

        prompt = (
            f"I'm cleaning up a knowledge graph. Below are entity candidates. "
            f"Many are garbage — code fragments, SQL snippets, CLI commands, generic words, "
            f"file paths, variable names, UUIDs, temp files, internal table names, etc.\n\n"
            f"Return ONLY the ones that are real, meaningful named entities worth remembering: "
            f"real people, real projects/products, specific technologies/frameworks, "
            f"real organizations, specific programming languages.\n\n"
            f"Candidates:\n{names_list}\n\n"
            f"Return JSON: {{\"keep\": [\"Name1\", \"Name2\", ...]}}\n"
            f"If none are worth keeping, return {{\"keep\": []}}"
        )

        print(f"\nFiltering batch {i // batch_size + 1} ({len(batch)} candidates)...")
        result = call_llm_json(prompt, system="You are a concise entity filter. Return only valid JSON.")

        if not result or "keep" not in result:
            print(f"  Sonnet filter failed for this batch — marking all as reject (safe default)")
            for c in batch:
                all_reject.append(c)
            continue

        keep_names = {n.lower() for n in result["keep"]}
        for c in batch:
            if c["name"].lower() in keep_names:
                all_keep.add(c["name"].lower())
            else:
                all_reject.append(c)

    # Report
    kept_candidates = [c for c in candidates if c["name"].lower() in all_keep]
    print(f"\n--- SONNET APPROVED ({len(kept_candidates)}) ---")
    for e in kept_candidates:
        print(f"  [{e['priority']}] {e['name']} ({e['type']})")

    print(f"\n--- REJECTING ({len(all_reject)}) ---")
    for e in all_reject:
        print(f"  [{e['priority']}] {e['name']} ({e['type']})")

    if args.apply:
        print(f"\nDeleting {len(all_reject)} garbage entities...")
        deleted = 0
        for e in all_reject:
            db.delete_entity(e["id"])
            deleted += 1
        print(f"Deleted {deleted} entities and their relationships.")
        print(f"KG now has {len(trusted) + len(kept_candidates)} entities.")
    else:
        print(f"\nDry run — use --apply to actually delete {len(all_reject)} entities.")


if __name__ == "__main__":
    main()
