"""Bootstrap the knowledge graph from existing session summaries.

Runs Haiku + GLiNER extraction over all sessions that have summaries,
populating the KG with entities and relationships from conversation history.

Usage:
    cd memorable/plugin
    python3 bootstrap_kg.py              # process all sessions
    python3 bootstrap_kg.py --limit 10   # process first 10 only (for testing)
    python3 bootstrap_kg.py --dry-run    # show what would be extracted without storing
"""

import argparse
import json
import time
from pathlib import Path
from collections import Counter

from server.db import MemorableDB
from server.config import Config
from server.kg import extract_from_session, haiku_extract, gliner_extract


def main():
    parser = argparse.ArgumentParser(description="Bootstrap KG from session summaries")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only N sessions (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract but don't store in database")
    parser.add_argument("--skip-haiku", action="store_true",
                        help="Skip Haiku extraction (GLiNER only)")
    args = parser.parse_args()

    config = Config()
    db = MemorableDB(
        Path(config["db_path"]),
        sync_url=config.get("sync_url", ""),
        auth_token=config.get("sync_auth_token", ""),
    )

    # Get current KG stats
    stats = db.get_stats()
    print(f"Current KG: {stats['kg_entities']} entities, {stats['kg_relationships']} relationships")
    print()

    # Load all sessions with summaries
    sessions = db.get_all_session_texts(limit=500)
    print(f"Found {len(sessions)} sessions")

    # Filter to sessions that have summaries (check both fields)
    sessions_with_text = []
    for s in sessions:
        # Newer sessions: Haiku summary in 'summary' field
        # Older sessions: LLMLingua compressed text in 'compressed_50'
        text = s.get("summary", "") or s.get("compressed_50", "")
        if text and len(text.strip()) > 50:
            s["_extract_text"] = text.strip()
            sessions_with_text.append(s)

    print(f"Sessions with summaries: {len(sessions_with_text)}")

    if args.limit:
        sessions_with_text = sessions_with_text[:args.limit]
        print(f"Processing first {args.limit} sessions")

    print()

    # Process each session
    total_entities = 0
    total_rels = 0
    all_entity_names = Counter()
    entity_types = Counter()
    errors = 0

    for i, session in enumerate(sessions_with_text, 1):
        title = session.get("title", "Untitled")
        date = session.get("date", "?")
        summary = session.get("_extract_text", "") or session.get("summary", "") or session.get("compressed_50", "")
        header = session.get("header", "")

        print(f"[{i}/{len(sessions_with_text)}] {date} — {title[:50]}")

        try:
            if args.dry_run:
                # Just extract, don't store
                result = haiku_extract(summary)
                gliner_ents = gliner_extract(summary)
                entities = result.get("entities", [])
                # Merge GLiNER
                seen = {e["name"].lower() for e in entities}
                for g in gliner_ents:
                    if g["name"].lower() not in seen:
                        entities.append(g)
                        seen.add(g["name"].lower())
                rels = result.get("relationships", [])

                for e in entities:
                    all_entity_names[e["name"]] += 1
                    entity_types[e.get("type", "concept")] += 1

                print(f"  → {len(entities)} entities, {len(rels)} relationships")
                if entities:
                    names = [e["name"] for e in entities[:8]]
                    print(f"    Entities: {', '.join(names)}")
                total_entities += len(entities)
                total_rels += len(rels)
            else:
                result = extract_from_session(
                    session_text=summary,
                    session_title=title,
                    session_header=header,
                    db=db,
                    priority=5,
                )
                added_e = result["entities_added"]
                added_r = result["relationships_added"]
                total_entities += added_e
                total_rels += added_r

                for e in result.get("entities", []):
                    all_entity_names[e["name"]] += 1
                    entity_types[e.get("type", "concept")] += 1

                if added_e or added_r:
                    print(f"  → +{added_e} entities, +{added_r} relationships")
                    if result.get("entities"):
                        names = [e["name"] for e in result["entities"][:8]]
                        print(f"    Entities: {', '.join(names)}")
                else:
                    print(f"  → (no new entities)")

        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

        # Brief pause between Haiku calls to avoid rate limiting
        if not args.dry_run and not args.skip_haiku:
            time.sleep(0.5)

    # Summary
    print()
    print("=" * 60)
    print(f"BOOTSTRAP COMPLETE")
    print(f"=" * 60)
    print(f"Sessions processed: {len(sessions_with_text)}")
    print(f"Entities {'found' if args.dry_run else 'added'}: {total_entities}")
    print(f"Relationships {'found' if args.dry_run else 'added'}: {total_rels}")
    print(f"Errors: {errors}")
    print()

    print("Entity type breakdown:")
    for etype, count in entity_types.most_common():
        print(f"  {etype}: {count}")
    print()

    # Show entities mentioned in multiple sessions
    multi_session = [(name, count) for name, count in all_entity_names.most_common()
                     if count >= 2]
    if multi_session:
        print(f"Entities mentioned in 2+ sessions ({len(multi_session)}):")
        for name, count in multi_session[:30]:
            print(f"  {name}: {count} sessions")

    if not args.dry_run:
        # Update priority for frequently mentioned entities
        for name, count in all_entity_names.most_common():
            if count >= 5:
                # Boost priority for entities mentioned in 5+ sessions
                new_priority = min(7, 5 + (count // 5))
                try:
                    entities = db.query_kg(entity=name, limit=1)
                    if entities and entities[0].get("priority", 0) < new_priority:
                        # Use add_entity which does upsert
                        db.add_entity(
                            entities[0]["name"],
                            entities[0]["type"],
                            description=entities[0].get("description", ""),
                            priority=new_priority,
                        )
                except Exception:
                    pass

        # Final stats
        stats = db.get_stats()
        print()
        print(f"Final KG: {stats['kg_entities']} entities, {stats['kg_relationships']} relationships")


if __name__ == "__main__":
    main()
