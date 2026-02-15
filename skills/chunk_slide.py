"""
skills/chunk_slide.py — Multi-granularity slide chunking.

Wraps src.index.chunker.SlideChunker.
"""

from typing import Optional

from src.dsl.models import PresentationNode
from src.index.chunker import SlideChunker

_chunker = SlideChunker()


def chunk(
    presentation: PresentationNode,
    source_file: Optional[str] = None,
) -> tuple:
    """Chunk a presentation at deck, slide, and element levels.

    Returns:
        Tuple of (DeckChunk, list[SlideChunk], list[ElementChunk]).
    """
    return _chunker.chunk(presentation, source_file=source_file)
