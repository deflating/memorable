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
    parser.add_argument("--model", type=str, default="sonnet",
                        help="Model to use for filtering (default: sonnet)")
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
            f"DEDUP: If you see duplicates with different casing or types "
            f"(e.g. 'spaCy' vs 'Spacy', or 'SQLite (tool)' vs 'SQLite (technology)'), "
            f"keep ONLY the best-cased version with the most appropriate type. "
            f"Return the canonical name exactly as it should appear.\n\n"
            f"Candidates:\n{names_list}\n\n"
            f"Return JSON: {{\"keep\": [\"Name1\", \"Name2\", ...]}}\n"
            f"If none are worth keeping, return {{\"keep\": []}}"
        )

        print(f"\nFiltering batch {i // batch_size + 1} ({len(batch)} candidates)...")
        result = call_llm_json(prompt, system="You are a concise entity filter. Return only valid JSON.", model=args.model)

        if not result or "keep" not in result:
            print(f"  LLM filter failed for this batch — marking all as reject (safe default)")
            for c in batch:
                all_reject.append(c)
            continue

        keep_names = {n.lower() for n in result["keep"]}
        for c in batch:
            if c["name"].lower() in keep_names:
                all_keep.add(c["name"].lower())
            else:
                all_reject.append(c)

    # Cross-batch dedup: group by lowercase name, keep highest-priority version
    kept_candidates = [c for c in candidates if c["name"].lower() in all_keep]
    def _casing_score(name: str) -> tuple[int, int, int]:
        """Higher = better casing. Returns (category, uppercase_count, mid_upper).
        Prefer mixed case > all upper > all lower, then more uppercase = more intentional,
        then prefer non-initial uppercase (brand casing like spaCy over Spacy)."""
        has_upper = any(c.isupper() for c in name)
        has_lower = any(c.islower() for c in name)
        upper_count = sum(1 for c in name if c.isupper())
        mid_upper = sum(1 for c in name[1:] if c.isupper())  # non-initial uppercase
        if has_upper and has_lower:
            return (2, upper_count, mid_upper)  # mixed case like "GLiNER", "spaCy"
        if has_upper:
            return (1, upper_count, mid_upper)  # all upper like "YAKE"
        return (0, 0, 0)                        # all lower like "gliner"

    seen_lower: dict[str, dict] = {}
    dedup_reject = []
    for c in kept_candidates:
        key = c["name"].lower()
        if key in seen_lower:
            existing = seen_lower[key]
            # Keep higher priority, then better casing, then longer name
            replace = False
            if c.get("priority", 0) > existing.get("priority", 0):
                replace = True
            elif c.get("priority", 0) == existing.get("priority", 0):
                if _casing_score(c["name"]) > _casing_score(existing["name"]):
                    replace = True
                elif _casing_score(c["name"]) == _casing_score(existing["name"]) and len(c["name"]) > len(existing["name"]):
                    replace = True
            if replace:
                dedup_reject.append(existing)
                seen_lower[key] = c
            else:
                dedup_reject.append(c)
        else:
            seen_lower[key] = c

    kept_candidates = list(seen_lower.values())
    all_reject.extend(dedup_reject)

    if dedup_reject:
        print(f"\n--- DEDUP: merging {len(dedup_reject)} duplicate(s) ---")
        for e in dedup_reject:
            print(f"  [{e['priority']}] {e['name']} ({e['type']}) → merged into {seen_lower[e['name'].lower()]['name']}")

    # Report
    print(f"\n--- APPROVED ({len(kept_candidates)}) ---")
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
