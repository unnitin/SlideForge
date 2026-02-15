"""
src/index/retriever.py — Design Index Retriever

Hybrid search combining:
  1. Semantic similarity (cosine on embeddings)
  2. Structural filters (SQL WHERE on metadata)
  3. Keyword matching (FTS5 full-text search)

Results are scored and ranked by a weighted combination.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import numpy as np

from src.index.store import DesignIndexStore


@dataclass
class SearchResult:
    """A single search result from the design index."""

    chunk_id: str
    chunk_type: str  # "deck", "slide", "element"
    score: float  # 0.0 - 1.0 combined relevance
    semantic_score: float = 0.0
    structural_score: float = 0.0
    keyword_score: float = 0.0

    # Content (populated from store)
    dsl_text: Optional[str] = None
    raw_content: Optional[dict] = None
    semantic_summary: str = ""
    topic_tags: list[str] = field(default_factory=list)

    # Context
    deck_title: Optional[str] = None
    slide_type: Optional[str] = None
    thumbnail_path: Optional[str] = None

    # Quality signals
    keep_count: int = 0
    regen_count: int = 0

    @property
    def quality_score(self) -> float:
        total = self.keep_count + self.regen_count
        return self.keep_count / total if total > 0 else 0.5


@dataclass
class SlideContext:
    """Full context of where a slide lives in its deck."""

    deck_title: str
    deck_summary: str
    slide_index: int
    total_slides: int
    prev_slide: Optional[dict] = None
    next_slide: Optional[dict] = None
    section_name: Optional[str] = None
    deck_position: str = "middle"


# ── Embedding function protocol ───────────────────────────────────

EmbedFn = Callable[[str], list[float]]


class DesignIndexRetriever:
    """
    Retrieves designs from the index using hybrid search.

    Usage:
        retriever = DesignIndexRetriever(store, embed_fn=my_embed_function)
        results = retriever.search("pipeline metrics dark background", limit=5)
    """

    # Scoring weights
    WEIGHT_SEMANTIC = 0.5
    WEIGHT_STRUCTURAL = 0.3
    WEIGHT_KEYWORD = 0.2
    QUALITY_BOOST = 0.1  # bonus for high-quality designs

    def __init__(self, store: DesignIndexStore, embed_fn: Optional[EmbedFn] = None):
        self.store = store
        self.embed_fn = embed_fn

    def search(
        self,
        query: str,
        granularity: Literal["deck", "slide", "element"] = "slide",
        filters: Optional[dict[str, Any]] = None,
        keywords: Optional[list[str]] = None,
        limit: int = 10,
        min_score: float = 0.1,
    ) -> list[SearchResult]:
        """
        Hybrid search across the design index.

        Args:
            query: Natural language search query.
            granularity: Which chunk level to search.
            filters: Structural filters (e.g. {"slide_type": "stat_callout"}).
            keywords: Additional FTS5 keywords.
            limit: Max results to return.
            min_score: Minimum combined score threshold.

        Returns:
            Ranked list of SearchResults.
        """
        table = f"{granularity}_chunks"
        candidates: dict[str, SearchResult] = {}

        # ── 1. Semantic search ─────────────────────────────────────
        if self.embed_fn:
            query_embedding = np.array(self.embed_fn(query), dtype=np.float32)
            all_embeddings = self.store.get_all_embeddings(table)

            for chunk_id, stored_embedding in all_embeddings:
                sim = _cosine_similarity(query_embedding, stored_embedding)
                if sim >= min_score * 0.5:  # loose pre-filter
                    candidates[chunk_id] = SearchResult(
                        chunk_id=chunk_id,
                        chunk_type=granularity,
                        score=0.0,
                        semantic_score=float(sim),
                    )

        # ── 2. Keyword search (FTS5) ──────────────────────────────
        fts_query = query
        if keywords:
            fts_query = " OR ".join([query] + keywords)

        try:
            fts_results = self.store.fts_search(table, fts_query, limit=limit * 3)
            for row in fts_results:
                chunk_id = row.get("id") or row.get("rowid")
                if chunk_id is None:
                    continue
                chunk_id = str(chunk_id)
                if chunk_id in candidates:
                    # Normalize FTS rank (negative, lower = better)
                    candidates[chunk_id].keyword_score = min(1.0, abs(row.get("rank", 0)) / 10)
                else:
                    candidates[chunk_id] = SearchResult(
                        chunk_id=chunk_id,
                        chunk_type=granularity,
                        score=0.0,
                        keyword_score=min(1.0, abs(row.get("rank", 0)) / 10),
                    )
        except Exception:
            pass  # FTS may not have data yet

        # ── 3. Structural filter ───────────────────────────────────
        if filters:
            for chunk_id, result in list(candidates.items()):
                row = self.store.conn.execute(
                    f"SELECT * FROM {table} WHERE id = ?", (chunk_id,)
                ).fetchone()
                if row is None:
                    continue
                row_dict = dict(row)
                match = all(str(row_dict.get(k)) == str(v) for k, v in filters.items())
                result.structural_score = 1.0 if match else 0.0

        # ── 4. Score and rank ──────────────────────────────────────
        for chunk_id, result in candidates.items():
            result.score = (
                self.WEIGHT_SEMANTIC * result.semantic_score
                + self.WEIGHT_STRUCTURAL * result.structural_score
                + self.WEIGHT_KEYWORD * result.keyword_score
            )

            # Quality boost
            if result.quality_score > 0.6:
                result.score += self.QUALITY_BOOST

        # ── 5. Hydrate top results ─────────────────────────────────
        ranked = sorted(candidates.values(), key=lambda r: r.score, reverse=True)
        ranked = [r for r in ranked if r.score >= min_score][:limit]

        for result in ranked:
            self._hydrate(result, granularity)

        return ranked

    def find_similar_slides(self, dsl_text: str, limit: int = 5) -> list[SearchResult]:
        """Find slides structurally and semantically similar to given DSL."""
        return self.search(dsl_text, granularity="slide", limit=limit)

    def get_slide_context(self, slide_chunk_id: str) -> Optional[SlideContext]:
        """Get full deck context for a slide."""
        slide = self.store.get_slide(slide_chunk_id)
        if not slide:
            return None

        deck = self.store.get_deck(slide["deck_chunk_id"])
        if not deck:
            return None

        all_slides = self.store.get_slides_for_deck(slide["deck_chunk_id"])

        idx = slide["slide_index"]
        prev_slide = all_slides[idx - 1] if idx > 0 else None
        next_slide = all_slides[idx + 1] if idx < len(all_slides) - 1 else None

        return SlideContext(
            deck_title=deck["title"],
            deck_summary=deck.get("narrative_summary", ""),
            slide_index=idx,
            total_slides=len(all_slides),
            prev_slide=prev_slide,
            next_slide=next_slide,
            section_name=slide.get("section_name"),
            deck_position=slide.get("deck_position", "middle"),
        )

    def suggest_next_slide(
        self,
        current_slide_types: list[str],
        limit: int = 3,
    ) -> list[SearchResult]:
        """Suggest what slide type should come next based on historical patterns."""
        if not current_slide_types:
            return self.search("title opening", granularity="slide", limit=limit)

        last_type = current_slide_types[-1]
        return self.search(
            f"slide that follows {last_type}",
            granularity="slide",
            filters={"prev_slide_type": last_type},
            limit=limit,
        )

    def get_best_design_for(
        self,
        content_type: str,
        topic: str,
        audience: Optional[str] = None,
    ) -> Optional[SearchResult]:
        """Get the single best proven design for a content type + topic."""
        query = f"{topic} {content_type}"
        if audience:
            query += f" {audience}"
        results = self.search(
            query,
            granularity="slide",
            filters={"slide_type": content_type},
            limit=1,
        )
        return results[0] if results else None

    # ── Internal ───────────────────────────────────────────────────

    def _hydrate(self, result: SearchResult, granularity: str):
        """Populate a SearchResult with full data from the store."""
        if granularity == "slide":
            row = self.store.get_slide(result.chunk_id)
            if row:
                result.dsl_text = row.get("dsl_text")
                result.semantic_summary = row.get("semantic_summary", "")
                result.slide_type = row.get("slide_type")
                result.thumbnail_path = row.get("thumbnail_path")
                result.keep_count = row.get("keep_count", 0)
                result.regen_count = row.get("regen_count", 0)
                try:
                    result.topic_tags = json.loads(row.get("topic_tags", "[]"))
                except (json.JSONDecodeError, TypeError):
                    pass
                # Get deck title
                deck = self.store.get_deck(row.get("deck_chunk_id", ""))
                if deck:
                    result.deck_title = deck.get("title")

        elif granularity == "element":
            row = self.store.conn.execute(
                "SELECT * FROM element_chunks WHERE id = ?", (result.chunk_id,)
            ).fetchone()
            if row:
                row = dict(row)
                result.semantic_summary = row.get("semantic_summary", "")
                result.slide_type = row.get("slide_type")
                try:
                    result.raw_content = json.loads(row.get("raw_content", "{}"))
                    result.topic_tags = json.loads(row.get("topic_tags", "[]"))
                except (json.JSONDecodeError, TypeError):
                    pass

        elif granularity == "deck":
            row = self.store.get_deck(result.chunk_id)
            if row:
                result.deck_title = row.get("title")
                result.semantic_summary = row.get("narrative_summary", "")
                try:
                    result.topic_tags = json.loads(row.get("topic_tags", "[]"))
                except (json.JSONDecodeError, TypeError):
                    pass


# ── Utilities ──────────────────────────────────────────────────────


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))
