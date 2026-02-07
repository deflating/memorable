#!/usr/bin/env python3
"""One-shot reprocessing script. Run and forget."""
import os, sys, time
os.environ["TOKENIZERS_PARALLELISM"] = "false"
sys.path.insert(0, os.path.dirname(__file__))

from server.processor import TranscriptProcessor
from server.config import Config

config = Config()
proc = TranscriptProcessor(config)
proc._scan_for_new_transcripts()

batch = 0
total_stored = 0
start = time.time()

while True:
    pending = proc.db.get_pending_transcripts(limit=50)
    if not pending:
        break
    batch += 1
    print(f"\nBatch {batch}: {len(pending)} transcripts...", flush=True)
    stored = 0
    for item in pending:
        try:
            proc._process_one(item)
            stored += 1
        except Exception as e:
            proc.db.mark_processed(item["id"], error=str(e))
    total_stored += stored
    elapsed = time.time() - start
    print(f"  +{stored} sessions ({total_stored} total, {elapsed:.0f}s elapsed)", flush=True)

elapsed = time.time() - start
stats = proc.db.get_stats()
print(f"\n=== DONE in {elapsed:.0f}s ===")
print(f"Sessions: {stats['sessions']}")
print(f"Words: {stats['total_words_processed']:,}")
print(f"Pending: {stats['pending_transcripts']}")
