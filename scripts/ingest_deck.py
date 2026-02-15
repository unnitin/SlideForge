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

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dsl.parser import SlideDSLParser
from src.index.chunker import SlideChunker
from src.index.store import DesignIndexStore


def ingest_sdsl(path: str, store: DesignIndexStore) -> str:
    """Ingest a .sdsl file. Returns deck_chunk_id."""
    parser = SlideDSLParser()
    pres = parser.parse_file(path)
    chunker = SlideChunker()
    deck, slides, elements = chunker.chunk(pres, source_file=path)

    store.upsert_deck(deck)
    for s in slides:
        store.upsert_slide(s)
    for e in elements:
        store.upsert_element(e)

    print(f"  Ingested: {deck.title}")
    print(f"  Slides: {len(slides)}, Elements: {len(elements)}")
    return deck.id


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest_deck.py <path>")
        sys.exit(1)

    target = Path(sys.argv[1])
    store = DesignIndexStore("design_index.db")
    store.initialize()

    if target.is_dir():
        files = list(target.glob("*.sdsl")) + list(target.glob("*.pptx"))
        print(f"Found {len(files)} files in {target}")
        for f in sorted(files):
            print(f"\nProcessing: {f.name}")
            if f.suffix == ".sdsl":
                ingest_sdsl(str(f), store)
            else:
                print("  SKIP: .pptx ingestion not yet implemented")
    elif target.suffix == ".sdsl":
        ingest_sdsl(str(target), store)
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
