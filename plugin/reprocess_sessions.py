"""Reprocess all transcripts with the current metadata pipeline.

Clears existing sessions and processing queue, then re-runs the full
processing pipeline (Haiku summaries + GLiNER metadata extraction +
Apple FM headers) on all transcripts.

Usage:
    cd memorable/plugin
    python3 reprocess_sessions.py              # dry run (shows what would happen)
    python3 reprocess_sessions.py --apply      # actually reprocess everything
    python3 reprocess_sessions.py --apply --batch 20  # process 20 at a time
"""

import argparse
import time
from pathlib import Path

from server.db import MemorableDB
from server.config import Config
from server.processor import TranscriptProcessor


def main():
    parser = argparse.ArgumentParser(description="Reprocess all transcripts with current metadata pipeline")
    parser.add_argument("--apply", action="store_true", help="Actually reprocess (default: dry run)")
    parser.add_argument("--batch", type=int, default=0,
                        help="Process N transcripts then stop (0 = all)")
    parser.add_argument("--keep-sessions", action="store_true",
                        help="Don't delete existing sessions (only reprocess unprocessed transcripts)")
    args = parser.parse_args()

    config = Config()
    db = MemorableDB(
        Path(config["db_path"]),
        sync_url=config.get("sync_url", ""),
        auth_token=config.get("sync_auth_token", ""),
    )

    stats = db.get_stats()
    print(f"Current state:")
    print(f"  Sessions: {stats['sessions']}")
    print(f"  Words processed: {stats['total_words_processed']:,}")
    print(f"  Pending transcripts: {stats['pending_transcripts']}")

    # Count available transcripts
    transcript_dirs = config["transcript_dirs"]
    all_jsonl = []
    for transcript_dir in transcript_dirs:
        base = Path(transcript_dir)
        if not base.exists():
            continue
        for project_dir in base.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl_file in project_dir.glob("*.jsonl"):
                all_jsonl.append(jsonl_file)

    print(f"  Transcript files found: {len(all_jsonl)}")

    if not args.apply:
        print(f"\nDry run — what would happen:")
        if not args.keep_sessions:
            print(f"  1. Delete all {stats['sessions']} existing sessions")
            print(f"  2. Clear processing queue")
        else:
            print(f"  1. Keep existing sessions")
            print(f"  2. Only process unprocessed transcripts")
        print(f"  3. Re-scan {len(all_jsonl)} transcript files")
        print(f"  4. Process with: Haiku + GLiNER + Apple FM")
        if args.batch:
            print(f"  5. Stop after {args.batch} transcripts")
        print(f"\nUse --apply to run. This will take a while (Haiku + GLiNER per session).")
        return

    # === APPLY MODE ===
    start = time.time()

    if not args.keep_sessions:
        # Step 1: Clear sessions and queue
        print(f"\nStep 1: Clearing {stats['sessions']} sessions and processing queue...")
        def clear_all(conn):
            conn.execute("DELETE FROM processing_queue")
            conn.execute("DELETE FROM sessions")
        db._execute(clear_all)
        print("  Done.")
    else:
        print("\nStep 1: Keeping existing sessions.")

    # Step 2: Create processor and run
    print(f"\nStep 2: Processing transcripts...")
    processor = TranscriptProcessor(config)

    if args.batch:
        # Scan first, then process in batches
        processor._scan_for_new_transcripts()
        pending = db.get_pending_transcripts(limit=args.batch)
        print(f"  Queued: {len(pending)} (batch limit: {args.batch})")
        for i, item in enumerate(pending, 1):
            try:
                print(f"\n  [{i}/{len(pending)}] {Path(item['transcript_path']).name[:30]}...")
                processor._process_one(item)
            except Exception as e:
                print(f"    Error: {e}")
                db.mark_processed(item["id"], error=str(e))
    else:
        # Loop until all transcripts are processed
        processor._scan_for_new_transcripts()
        total_pending = db.get_stats()["pending_transcripts"]
        processed = 0
        while True:
            pending = db.get_pending_transcripts(limit=50)
            if not pending:
                break
            for i, item in enumerate(pending, 1):
                processed += 1
                try:
                    print(f"\n  [{processed}/{total_pending}] {Path(item['transcript_path']).name[:30]}...")
                    processor._process_one(item)
                except Exception as e:
                    print(f"    Error: {e}")
                    db.mark_processed(item["id"], error=str(e))

    elapsed = time.time() - start
    new_stats = db.get_stats()
    print(f"\n--- Done in {elapsed:.0f}s ---")
    print(f"  Sessions: {stats['sessions']} → {new_stats['sessions']}")
    print(f"  Words: {stats['total_words_processed']:,} → {new_stats['total_words_processed']:,}")
    print(f"  Pending: {new_stats['pending_transcripts']}")


if __name__ == "__main__":
    main()
