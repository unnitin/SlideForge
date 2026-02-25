"""
src/index/embeddings.py — Embedding function factory.

Provides a backend-agnostic embedding function for the design index.

Backends (in priority order):
  - sentence-transformers: best quality, requires `pip install sentence-transformers`
  - hash: deterministic n-gram hash vector, no extra deps, good for dev/testing

Usage:
    from src.index.embeddings import make_embed_fn

    embed = make_embed_fn()           # auto-selects best available
    vec = embed("pipeline metrics")   # -> list[float] of length 384
"""

from __future__ import annotations

import hashlib
import logging
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

# Vector dimension — matches all-MiniLM-L6-v2 so hash fallback is compatible
_DIM = 384

EmbedFn = Callable[[str], list[float]]


def make_embed_fn(
    backend: str = "auto",
    model: str = "all-MiniLM-L6-v2",
) -> EmbedFn:
    """
    Create and return an embedding function.

    Args:
        backend: "auto" | "sentence_transformers" | "hash".
                 "auto" tries sentence-transformers first, then hash.
        model: sentence-transformers model name (ignored for hash backend).

    Returns:
        Callable (text: str) -> list[float]

    Raises:
        RuntimeError: if backend="sentence_transformers" but it's not installed.
    """
    if backend == "hash":
        logger.info("Using hash embedding backend (dim=%d)", _DIM)
        return _hash_embed

    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        st_model = SentenceTransformer(model)
        logger.info(
            "Using sentence-transformers backend: %s (dim=%d)",
            model,
            st_model.get_sentence_embedding_dimension(),
        )

        def _st_embed(text: str) -> list[float]:
            vec = st_model.encode(text, normalize_embeddings=True)
            return vec.tolist()

        return _st_embed

    except ImportError:
        if backend == "sentence_transformers":
            raise RuntimeError(
                "sentence-transformers is not installed. Run: pip install sentence-transformers"
            )
        logger.warning(
            "sentence-transformers not installed; using hash embeddings. "
            "For better retrieval: pip install sentence-transformers"
        )
        return _hash_embed


def embed_chunks(
    chunks: list,
    embed_fn: EmbedFn,
) -> None:
    """
    Compute and attach embeddings to a list of chunk objects in-place.

    Works with DeckChunk, SlideChunk, and ElementChunk — any object that has
    an `embedding_text()` method and an `embedding` attribute.

    Args:
        chunks: List of chunk objects to embed.
        embed_fn: Embedding function from make_embed_fn().
    """
    for chunk in chunks:
        try:
            text = chunk.embedding_text()
            chunk.embedding = embed_fn(text)
        except Exception as exc:
            logger.warning("Failed to embed chunk %s: %s", getattr(chunk, "id", "?"), exc)


# ── Hash-based fallback ────────────────────────────────────────────


def _hash_embed(text: str) -> list[float]:
    """
    Deterministic hash-based pseudo-embedding (no extra dependencies).

    Uses character-level unigrams and bigrams hashed into a fixed-size float
    vector, then L2-normalized. Gives reproducible, structurally meaningful
    vectors for keyword/topic matching — not true semantic similarity.

    Dimension: 384 (compatible with all-MiniLM-L6-v2 slot in the store).
    """
    vec = np.zeros(_DIM, dtype=np.float32)
    tokens = text.lower().split()

    if not tokens:
        return vec.tolist()

    ngrams = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
    token_freq: dict[str, int] = {}
    for t in ngrams:
        token_freq[t] = token_freq.get(t, 0) + 1

    for gram, freq in token_freq.items():
        digest = hashlib.md5(gram.encode()).digest()
        idx = int.from_bytes(digest[:2], "little") % _DIM
        # IDF-lite: down-weight high-frequency terms
        vec[idx] += 1.0 / (1.0 + freq)

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm

    return vec.tolist()
