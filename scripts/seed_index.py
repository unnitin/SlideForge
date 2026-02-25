#!/usr/bin/env python3
"""
scripts/seed_index.py — Bootstrap the design index from a directory of decks.

Usage:
    python scripts/seed_index.py ./decks/

TODO (Claude Code Phase 2):
    - Batch ingestion with progress bar
    - Parallel semantic enrichment via Index Curator
    - Batch embedding computation
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.ingest_deck import ingest_sdsl
from src.index.embeddings import make_embed_fn
from src.index.store import DesignIndexStore


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Bootstrap the design index from a directory of .sdsl files."
    )
    ap.add_argument("directory", help="Directory containing .sdsl files (searched recursively)")
    ap.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embedding computation",
    )
    ap.add_argument(
        "--embed-backend",
        default="auto",
        choices=["auto", "sentence_transformers", "hash"],
        help="Embedding backend (default: auto)",
    )
    args = ap.parse_args()

    deck_dir = Path(args.directory)
    if not deck_dir.is_dir():
        print(f"ERROR: {deck_dir} is not a directory")
        sys.exit(1)

    store = DesignIndexStore("design_index.db")
    store.initialize()

    embed_fn = None
    if not args.no_embed:
        print(f"Initializing embeddings (backend={args.embed_backend})...")
        embed_fn = make_embed_fn(backend=args.embed_backend)

    files = sorted(deck_dir.glob("**/*.sdsl"))
    print(f"Seeding index from {len(files)} .sdsl files in {deck_dir}\n")

    ok, failed = 0, 0
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        try:
            ingest_sdsl(str(f), store, embed_fn=embed_fn)
            ok += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    stats = store.get_stats()
    print(f"\nDone. Ingested: {ok}, Failed: {failed}")
    print(f"Index stats: {stats}")
    store.close()


if __name__ == "__main__":
    main()
