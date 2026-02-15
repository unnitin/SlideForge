"""
skills/index_search.py — Semantic and structural search over the design index.

Wraps src.index.store.DesignIndexStore and src.index.retriever.DesignIndexRetriever.
"""

from typing import Callable, Literal, Optional

from src.index.retriever import DesignIndexRetriever, SlideContext
from src.index.store import DesignIndexStore


def open_index(
    db_path: str = "design_index.db",
    embed_fn: Optional[Callable[[str], list]] = None,
) -> tuple:
    """Open the design index and create a retriever.

    Returns:
        Tuple of (DesignIndexStore, DesignIndexRetriever).
    """
    store = DesignIndexStore(db_path)
    store.initialize()
    retriever = DesignIndexRetriever(store, embed_fn=embed_fn)
    return store, retriever


def search(
    retriever: DesignIndexRetriever,
    query: str,
    granularity: Literal["deck", "slide", "element"] = "slide",
    filters: Optional[dict] = None,
    keywords: Optional[list] = None,
    limit: int = 10,
) -> list:
    """Search the design index with hybrid ranking."""
    return retriever.search(
        query=query,
        granularity=granularity,
        filters=filters,
        keywords=keywords,
        limit=limit,
    )


def get_slide_context(
    retriever: DesignIndexRetriever,
    slide_chunk_id: str,
) -> Optional[SlideContext]:
    """Get full deck context for a slide chunk."""
    return retriever.get_slide_context(slide_chunk_id)
