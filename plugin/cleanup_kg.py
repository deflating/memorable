"""Retroactive KG cleanup — runs all existing entities through Sonnet filter.

All entities go through Sonnet for validation. Garbage (code fragments, SQL
snippets, CLI commands, generic words, file paths, etc.) gets deleted.

Usage:
    cd memorable/plugin
    python3 cleanup_kg.py              # dry run (shows what would be deleted)
    python3 cleanup_kg.py --apply      # actually delete garbage
"""

import argparse
from pathlib import Path

from server.db import MemorableDB
from server.config import Config
from server.llm import call_llm_json


def main():
    parser = argparse.ArgumentParser(description="Clean up KG entities via Sonnet filter")
    parser.add_argument("--apply", action="store_true", help="Actually delete garbage (default: dry run)")
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

    if not all_entities:
        print("Nothing to clean up.")
        return

    # Batch all entities through Sonnet
    batch_size = 30
    all_keep = set()
    all_reject = []

    for i in range(0, len(all_entities), batch_size):
        batch = all_entities[i:i + batch_size]
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
            print(f"  LLM filter failed for this batch — skipping (safe default)")
            continue

        keep_names = {n.lower() for n in result["keep"]}
        for c in batch:
            if c["name"].lower() in keep_names:
                all_keep.add(c["name"].lower())
            else:
                all_reject.append(c)

    # Cross-batch dedup: group by lowercase name, keep best casing
    kept = [c for c in all_entities if c["name"].lower() in all_keep]

    def _casing_score(name: str) -> tuple[int, int, int]:
        has_upper = any(c.isupper() for c in name)
        has_lower = any(c.islower() for c in name)
        upper_count = sum(1 for c in name if c.isupper())
        mid_upper = sum(1 for c in name[1:] if c.isupper())
        if has_upper and has_lower:
            return (2, upper_count, mid_upper)
        if has_upper:
            return (1, upper_count, mid_upper)
        return (0, 0, 0)

    seen_lower: dict[str, dict] = {}
    dedup_reject = []
    for c in kept:
        key = c["name"].lower()
        if key in seen_lower:
            existing = seen_lower[key]
            if _casing_score(c["name"]) > _casing_score(existing["name"]):
                dedup_reject.append(existing)
                seen_lower[key] = c
            else:
                dedup_reject.append(c)
        else:
            seen_lower[key] = c

    kept = list(seen_lower.values())
    all_reject.extend(dedup_reject)

    if dedup_reject:
        print(f"\n--- DEDUP: merging {len(dedup_reject)} duplicate(s) ---")
        for e in dedup_reject:
            print(f"  {e['name']} ({e['type']}) → merged into {seen_lower[e['name'].lower()]['name']}")

    print(f"\n--- APPROVED ({len(kept)}) ---")
    for e in kept:
        print(f"  {e['name']} ({e['type']})")

    print(f"\n--- REJECTING ({len(all_reject)}) ---")
    for e in all_reject:
        print(f"  {e['name']} ({e['type']})")

    if args.apply:
        print(f"\nDeleting {len(all_reject)} garbage entities...")
        deleted = 0
        for e in all_reject:
            db.delete_entity(e["id"])
            deleted += 1
        print(f"Deleted {deleted} entities and their relationships.")
        print(f"KG now has {len(kept)} entities.")
    else:
        print(f"\nDry run — use --apply to actually delete {len(all_reject)} entities.")


if __name__ == "__main__":
    main()
