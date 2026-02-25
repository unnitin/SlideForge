#!/usr/bin/env python3
"""
scripts/ingest_deck.py — Ingest an existing .pptx or .sdsl into the design index.

Usage:
    python scripts/ingest_deck.py path/to/deck.pptx
    python scripts/ingest_deck.py path/to/deck.sdsl
    python scripts/ingest_deck.py ./decks/       # ingest a whole directory

TODO (Claude Code Phase 2):
    - .pptx ingestion: extract text/structure via markitdown → generate DSL → chunk
    - Semantic enrichment: call Index Curator agent after chunking
    - Embedding generation: compute embeddings for all chunks
    - Thumbnail generation: render slides to images for visual reference
"""

import sys
from pathlib import Path
from typing import Optional

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dsl.parser import SlideForgeParser
from src.index.chunker import SlideChunker
from src.index.embeddings import EmbedFn, embed_chunks, make_embed_fn
from src.index.store import DesignIndexStore


def ingest_sdsl(
    path: str,
    store: DesignIndexStore,
    embed_fn: Optional[EmbedFn] = None,
) -> str:
    """Ingest a .sdsl file into the design index. Returns deck_chunk_id."""
    parser = SlideForgeParser()
    pres = parser.parse_file(path)
    chunker = SlideChunker()
    deck, slides, elements = chunker.chunk(pres, source_file=path)

    if embed_fn:
        embed_chunks([deck] + slides + elements, embed_fn)

    store.upsert_deck(deck)
    for s in slides:
        store.upsert_slide(s)
    for e in elements:
        store.upsert_element(e)

    embedded = "with embeddings" if embed_fn else "without embeddings"
    print(f"  Ingested: {deck.title} ({embedded})")
    print(f"  Slides: {len(slides)}, Elements: {len(elements)}")
    return deck.id


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Ingest .sdsl files into the design index.")
    ap.add_argument("path", help=".sdsl file or directory to ingest")
    ap.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embedding computation (faster, disables semantic search)",
    )
    ap.add_argument(
        "--embed-backend",
        default="auto",
        choices=["auto", "sentence_transformers", "hash"],
        help="Embedding backend (default: auto)",
    )
    args = ap.parse_args()

    target = Path(args.path)
    store = DesignIndexStore("design_index.db")
    store.initialize()

    embed_fn: Optional[EmbedFn] = None
    if not args.no_embed:
        print(f"Initializing embeddings (backend={args.embed_backend})...")
        embed_fn = make_embed_fn(backend=args.embed_backend)

    if target.is_dir():
        files = list(target.glob("**/*.sdsl")) + list(target.glob("**/*.pptx"))
        print(f"Found {len(files)} files in {target}")
        for f in sorted(files):
            print(f"\nProcessing: {f.name}")
            if f.suffix == ".sdsl":
                ingest_sdsl(str(f), store, embed_fn=embed_fn)
            else:
                print("  SKIP: .pptx ingestion not yet implemented")
    elif target.suffix == ".sdsl":
        ingest_sdsl(str(target), store, embed_fn=embed_fn)
    elif target.suffix == ".pptx":
        print("ERROR: .pptx ingestion not yet implemented. Convert to .sdsl first.")
        sys.exit(1)
    else:
        print(f"ERROR: Unsupported file type: {target.suffix}")
        sys.exit(1)

    stats = store.get_stats()
    print(f"\nIndex stats: {stats}")
    store.close()


if __name__ == "__main__":
    main()
