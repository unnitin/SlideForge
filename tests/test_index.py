"""
tests/test_index.py — Tests for Design Index store and retriever

Tests the SQLite store (CRUD, FTS, feedback) and the retriever
(hybrid search, slide context, similarity) using the sample.sdsl fixture.
"""

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dsl.parser import SlideForgeParser
from src.index.chunker import SlideChunker
from src.index.retriever import DesignIndexRetriever, _cosine_similarity
from src.index.store import DesignIndexStore

SAMPLE_PATH = Path(__file__).parent.parent / "docs" / "examples" / "sample.sdsl"


def _make_store() -> DesignIndexStore:
    """Create a fresh in-memory store."""
    store = DesignIndexStore(":memory:")
    store.initialize()
    return store


def _ingest_sample(store: DesignIndexStore):
    """Parse sample.sdsl, chunk it, and insert into the store."""
    parser = SlideForgeParser()
    pres = parser.parse(SAMPLE_PATH.read_text(encoding="utf-8"))
    chunker = SlideChunker()
    deck, slides, elements = chunker.chunk(pres, source_file=str(SAMPLE_PATH))

    store.upsert_deck(deck)
    for s in slides:
        store.upsert_slide(s)
    for e in elements:
        store.upsert_element(e)

    return deck, slides, elements


# ── Store: Schema & Init ──────────────────────────────────────────


class TestStoreInit:
    def test_initialize_creates_tables(self):
        store = _make_store()
        tables = store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r["name"] for r in tables}
        assert "deck_chunks" in names
        assert "slide_chunks" in names
        assert "element_chunks" in names
        assert "phrase_triggers" in names
        assert "feedback_log" in names
        store.close()

    def test_double_initialize_is_safe(self):
        store = _make_store()
        store.initialize()  # second time should not raise
        store.close()


# ── Store: Deck CRUD ──────────────────────────────────────────────


class TestStoreDeck:
    def test_upsert_and_get_deck(self):
        store = _make_store()
        deck, _, _ = _ingest_sample(store)
        fetched = store.get_deck(deck.id)
        assert fetched is not None
        assert fetched["title"] == "Q3 2025 Data Platform Update"
        store.close()

    def test_get_nonexistent_deck(self):
        store = _make_store()
        assert store.get_deck("nonexistent") is None
        store.close()

    def test_deck_fields_persisted(self):
        store = _make_store()
        deck, _, _ = _ingest_sample(store)
        fetched = store.get_deck(deck.id)
        assert fetched["author"] == "Nitin"
        assert fetched["company"] == "Create Music Group"
        assert fetched["slide_count"] == 11
        store.close()


# ── Store: Slide CRUD ─────────────────────────────────────────────


class TestStoreSlide:
    def test_upsert_and_get_slide(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        fetched = store.get_slide(slides[0].id)
        assert fetched is not None
        assert fetched["slide_type"] == "title"
        store.close()

    def test_get_slides_for_deck(self):
        store = _make_store()
        deck, slides, _ = _ingest_sample(store)
        fetched = store.get_slides_for_deck(deck.id)
        assert len(fetched) == len(slides)
        # Should be ordered by slide_index
        for i, row in enumerate(fetched):
            assert row["slide_index"] == i
        store.close()

    def test_structural_fields_persisted(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        stat_slide = store.get_slide(slides[2].id)
        assert stat_slide["has_stats"] == 1
        assert stat_slide["stat_count"] == 3
        store.close()


# ── Store: Element CRUD ───────────────────────────────────────────


class TestStoreElement:
    def test_get_elements_for_slide(self):
        store = _make_store()
        _, slides, elements = _ingest_sample(store)
        # Get elements for the stat_callout slide
        slide_id = slides[2].id
        fetched = store.get_elements_for_slide(slide_id)
        expected = [e for e in elements if e.slide_chunk_id == slide_id]
        assert len(fetched) == len(expected)
        store.close()

    def test_element_ordering(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        fetched = store.get_elements_for_slide(slides[2].id)
        positions = [r["position_in_slide"] for r in fetched]
        assert positions == sorted(positions)
        store.close()


# ── Store: Embeddings ─────────────────────────────────────────────


class TestStoreEmbeddings:
    def test_store_and_retrieve_embedding(self):
        store = _make_store()
        deck, slides, _ = _ingest_sample(store)

        # Add a fake embedding to a slide
        fake_embed = [0.1] * 128
        slides[0].embedding = fake_embed
        store.upsert_slide(slides[0])

        results = store.get_all_embeddings("slide_chunks")
        assert len(results) >= 1
        ids = [r[0] for r in results]
        assert slides[0].id in ids
        # Check it round-trips correctly
        vec = dict(results)[slides[0].id]
        np.testing.assert_allclose(vec, fake_embed, atol=1e-6)
        store.close()

    def test_no_embeddings_returns_empty(self):
        store = _make_store()
        _ingest_sample(store)
        # No embeddings were set, so only non-null ones returned
        results = store.get_all_embeddings("deck_chunks")
        assert len(results) == 0
        store.close()


# ── Store: Phrase Triggers ────────────────────────────────────────


class TestStorePhrases:
    def test_record_phrase_trigger(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        store.record_phrase_trigger("show me pipeline metrics", slide_chunk_id=slides[2].id)
        stats = store.get_stats()
        assert stats["phrase_triggers"] == 1
        store.close()

    def test_duplicate_phrase_increments_count(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        store.record_phrase_trigger("pipeline metrics", slide_chunk_id=slides[2].id)
        store.record_phrase_trigger("pipeline metrics", slide_chunk_id=slides[2].id)
        row = store.conn.execute("SELECT hit_count FROM phrase_triggers").fetchone()
        assert row["hit_count"] == 2
        store.close()


# ── Store: Feedback ───────────────────────────────────────────────


class TestStoreFeedback:
    def test_record_keep(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        store.record_feedback(slides[0].id, "slide", "keep")
        row = store.get_slide(slides[0].id)
        assert row["keep_count"] == 1
        store.close()

    def test_record_regen(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        store.record_feedback(slides[0].id, "slide", "regen")
        row = store.get_slide(slides[0].id)
        assert row["regen_count"] == 1
        store.close()

    def test_feedback_log_persisted(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        store.record_feedback(slides[0].id, "slide", "edit", context={"field": "heading"})
        row = store.conn.execute("SELECT * FROM feedback_log").fetchone()
        assert row["signal"] == "edit"
        assert row["chunk_type"] == "slide"
        store.close()


# ── Store: FTS Search ─────────────────────────────────────────────


class TestStoreFTS:
    def test_deck_fts_search(self):
        store = _make_store()
        deck, _, _ = _ingest_sample(store)
        # FTS should find the deck by title
        results = store.fts_search("deck_chunks", "Data Platform")
        assert len(results) >= 1
        store.close()

    def test_stats_counts(self):
        store = _make_store()
        _ingest_sample(store)
        stats = store.get_stats()
        assert stats["deck_chunks"] == 1
        assert stats["slide_chunks"] == 11
        assert stats["element_chunks"] > 0
        store.close()


# ── Store: File-based persistence ─────────────────────────────────


class TestStorePersistence:
    def test_file_based_store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        store = DesignIndexStore(db_path)
        store.initialize()
        _ingest_sample(store)
        store.close()

        # Reopen and verify data persists
        store2 = DesignIndexStore(db_path)
        stats = store2.get_stats()
        assert stats["deck_chunks"] == 1
        assert stats["slide_chunks"] == 11
        store2.close()
        Path(db_path).unlink()


# ── Retriever: Cosine Similarity ──────────────────────────────────


class TestCosine:
    def test_identical_vectors(self):
        v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_zero_vector(self):
        a = np.array([1.0, 2.0], dtype=np.float32)
        z = np.array([0.0, 0.0], dtype=np.float32)
        assert _cosine_similarity(a, z) == 0.0


# ── Retriever: Search ─────────────────────────────────────────────


def _dummy_embed(text: str) -> list[float]:
    """Deterministic pseudo-embedding for testing."""
    np.random.seed(hash(text) % 2**31)
    return np.random.randn(64).tolist()


class TestRetrieverSearch:
    def _setup(self):
        store = _make_store()
        deck, slides, elements = _ingest_sample(store)
        # Add embeddings to all slides
        for s in slides:
            s.embedding = _dummy_embed(s.embedding_text())
            store.upsert_slide(s)
        retriever = DesignIndexRetriever(store, embed_fn=_dummy_embed)
        return store, retriever, slides

    def test_search_returns_results(self):
        store, retriever, _ = self._setup()
        results = retriever.search("pipeline metrics", granularity="slide", limit=5)
        assert isinstance(results, list)
        # Should find some results via keyword or semantic match
        store.close()

    def test_search_with_structural_filter(self):
        store, retriever, _ = self._setup()
        results = retriever.search(
            "metrics",
            granularity="slide",
            filters={"slide_type": "stat_callout"},
            limit=10,
        )
        for r in results:
            if r.structural_score > 0:
                assert r.slide_type == "stat_callout"
        store.close()

    def test_search_respects_limit(self):
        store, retriever, _ = self._setup()
        results = retriever.search("data", granularity="slide", limit=3)
        assert len(results) <= 3
        store.close()

    def test_search_without_embeddings(self):
        store = _make_store()
        _ingest_sample(store)
        # No embed_fn, no embeddings stored — should still work via FTS
        retriever = DesignIndexRetriever(store, embed_fn=None)
        results = retriever.search("platform", granularity="slide", limit=5, min_score=0.0)
        # Should not crash
        assert isinstance(results, list)
        store.close()

    def test_results_are_sorted_by_score(self):
        store, retriever, _ = self._setup()
        results = retriever.search("data platform", granularity="slide", limit=10, min_score=0.0)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
        store.close()


# ── Retriever: Slide Context ─────────────────────────────────────


class TestRetrieverContext:
    def test_get_slide_context(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        retriever = DesignIndexRetriever(store)
        ctx = retriever.get_slide_context(slides[3].id)
        assert ctx is not None
        assert ctx.deck_title == "Q3 2025 Data Platform Update"
        assert ctx.slide_index == 3
        assert ctx.total_slides == 11
        assert ctx.prev_slide is not None
        assert ctx.next_slide is not None
        store.close()

    def test_context_for_first_slide(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        retriever = DesignIndexRetriever(store)
        ctx = retriever.get_slide_context(slides[0].id)
        assert ctx.prev_slide is None
        assert ctx.next_slide is not None
        assert ctx.deck_position == "opening"
        store.close()

    def test_context_for_last_slide(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        retriever = DesignIndexRetriever(store)
        ctx = retriever.get_slide_context(slides[-1].id)
        assert ctx.prev_slide is not None
        assert ctx.next_slide is None
        assert ctx.deck_position == "closing"
        store.close()

    def test_nonexistent_slide_returns_none(self):
        store = _make_store()
        _ingest_sample(store)
        retriever = DesignIndexRetriever(store)
        assert retriever.get_slide_context("nonexistent") is None
        store.close()


# ── Retriever: Suggest Next Slide ─────────────────────────────────


class TestRetrieverSuggest:
    def test_suggest_next_from_empty(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        for s in slides:
            s.embedding = _dummy_embed(s.embedding_text())
            store.upsert_slide(s)
        retriever = DesignIndexRetriever(store, embed_fn=_dummy_embed)
        results = retriever.suggest_next_slide([], limit=3)
        assert isinstance(results, list)
        store.close()

    def test_suggest_next_after_section_divider(self):
        store = _make_store()
        _, slides, _ = _ingest_sample(store)
        for s in slides:
            s.embedding = _dummy_embed(s.embedding_text())
            store.upsert_slide(s)
        retriever = DesignIndexRetriever(store, embed_fn=_dummy_embed)
        results = retriever.suggest_next_slide(["section_divider"], limit=3)
        assert isinstance(results, list)
        store.close()
