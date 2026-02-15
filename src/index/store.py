"""
src/index/store.py — Design Index Storage

SQLite for structured metadata + FTS5 for keyword search + BLOB
columns for embedding vectors. Designed to be swappable to
Cloud SQL + Vertex AI Vector Search on GCP later.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

import numpy as np

from src.index.chunker import DeckChunk, ElementChunk, SlideChunk


class DesignIndexStore:
    """
    Persistent storage for the design index.

    Stores chunks at three granularities with:
    - Structured metadata (SQLite columns)
    - Full-text search (FTS5 virtual tables)
    - Vector embeddings (BLOB columns, numpy serialized)
    """

    def __init__(self, db_path: str = "design_index.db"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def initialize(self):
        """Create all tables and indexes."""
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Write Operations ───────────────────────────────────────────

    def upsert_deck(self, chunk: DeckChunk):
        """Insert or update a deck chunk."""
        embedding_blob = _embed_to_blob(chunk.embedding) if chunk.embedding else None
        self.conn.execute(
            """INSERT OR REPLACE INTO deck_chunks
               (id, source_file, title, author, company, created_at,
                narrative_summary, audience, purpose, slide_count,
                slide_type_sequence, topic_tags, template_used,
                brand_colors, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk.id,
                chunk.source_file,
                chunk.title,
                chunk.author,
                chunk.company,
                chunk.created_at,
                chunk.narrative_summary,
                chunk.audience,
                chunk.purpose,
                chunk.slide_count,
                json.dumps(chunk.slide_type_sequence),
                json.dumps(chunk.topic_tags),
                chunk.template_used,
                json.dumps(chunk.brand_colors),
                embedding_blob,
            ),
        )
        # Update FTS
        self.conn.execute(
            """INSERT OR REPLACE INTO deck_chunks_fts
               (rowid, title, narrative_summary, audience, purpose, topic_tags)
               VALUES (
                 (SELECT rowid FROM deck_chunks WHERE id = ?),
                 ?, ?, ?, ?, ?)""",
            (
                chunk.id,
                chunk.title,
                chunk.narrative_summary,
                chunk.audience,
                chunk.purpose,
                json.dumps(chunk.topic_tags),
            ),
        )
        self.conn.commit()

    def upsert_slide(self, chunk: SlideChunk):
        """Insert or update a slide chunk."""
        embedding_blob = _embed_to_blob(chunk.embedding) if chunk.embedding else None
        self.conn.execute(
            """INSERT OR REPLACE INTO slide_chunks
               (id, deck_chunk_id, slide_index, slide_name, slide_type,
                layout_variant, background, semantic_summary, topic_tags,
                content_domain, has_stats, stat_count, has_bullets, bullet_count,
                has_columns, column_count, has_timeline, step_count,
                has_comparison, has_image, has_icons, dsl_text, thumbnail_path,
                color_palette, prev_slide_type, next_slide_type, section_name,
                deck_position, use_count, keep_count, edit_count, regen_count,
                embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk.id,
                chunk.deck_chunk_id,
                chunk.slide_index,
                chunk.slide_name,
                chunk.slide_type,
                chunk.layout_variant,
                chunk.background,
                chunk.semantic_summary,
                json.dumps(chunk.topic_tags),
                chunk.content_domain,
                int(chunk.has_stats),
                chunk.stat_count,
                int(chunk.has_bullets),
                chunk.bullet_count,
                int(chunk.has_columns),
                chunk.column_count,
                int(chunk.has_timeline),
                chunk.step_count,
                int(chunk.has_comparison),
                int(chunk.has_image),
                int(chunk.has_icons),
                chunk.dsl_text,
                chunk.thumbnail_path,
                json.dumps(chunk.color_palette),
                chunk.prev_slide_type,
                chunk.next_slide_type,
                chunk.section_name,
                chunk.deck_position,
                chunk.use_count,
                chunk.keep_count,
                chunk.edit_count,
                chunk.regen_count,
                embedding_blob,
            ),
        )
        self.conn.commit()

    def upsert_element(self, chunk: ElementChunk):
        """Insert or update an element chunk."""
        embedding_blob = _embed_to_blob(chunk.embedding) if chunk.embedding else None
        self.conn.execute(
            """INSERT OR REPLACE INTO element_chunks
               (id, slide_chunk_id, deck_chunk_id, element_type,
                semantic_summary, topic_tags, raw_content, visual_treatment,
                slide_type, position_in_slide, sibling_count, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk.id,
                chunk.slide_chunk_id,
                chunk.deck_chunk_id,
                chunk.element_type,
                chunk.semantic_summary,
                json.dumps(chunk.topic_tags),
                json.dumps(chunk.raw_content, default=str),
                json.dumps(chunk.visual_treatment),
                chunk.slide_type,
                chunk.position_in_slide,
                chunk.sibling_count,
                embedding_blob,
            ),
        )
        self.conn.commit()

    def record_phrase_trigger(
        self,
        phrase: str,
        slide_chunk_id: Optional[str] = None,
        element_chunk_id: Optional[str] = None,
    ):
        """Record a phrase → design mapping."""
        import uuid
        from datetime import datetime, timezone

        normalized = _normalize_phrase(phrase)
        now = datetime.now(timezone.utc).isoformat()

        # Check for existing
        row = self.conn.execute(
            "SELECT id, hit_count FROM phrase_triggers WHERE normalized_phrase = ?",
            (normalized,),
        ).fetchone()

        if row:
            self.conn.execute(
                "UPDATE phrase_triggers SET hit_count = hit_count + 1, updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
        else:
            self.conn.execute(
                """INSERT INTO phrase_triggers
                   (id, phrase, normalized_phrase, matched_slide_chunk_id,
                    matched_element_chunk_id, confidence, hit_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0.5, 1, ?, ?)""",
                (str(uuid.uuid4()), phrase, normalized, slide_chunk_id, element_chunk_id, now, now),
            )
        self.conn.commit()

    def record_feedback(
        self,
        chunk_id: str,
        chunk_type: str,
        signal: str,
        context: Optional[dict] = None,
    ):
        """Record a user feedback signal."""
        import uuid
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO feedback_log (id, chunk_id, chunk_type, signal, context, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                chunk_id,
                chunk_type,
                signal,
                json.dumps(context) if context else None,
                now,
            ),
        )

        # Update aggregate counts on the chunk
        if chunk_type == "slide" and signal in ("keep", "edit", "regen"):
            col = f"{signal}_count"
            self.conn.execute(
                f"UPDATE slide_chunks SET {col} = {col} + 1 WHERE id = ?",
                (chunk_id,),
            )

        self.conn.commit()

    # ── Read Operations ────────────────────────────────────────────

    def get_deck(self, deck_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM deck_chunks WHERE id = ?", (deck_id,)).fetchone()
        return dict(row) if row else None

    def get_slide(self, slide_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM slide_chunks WHERE id = ?", (slide_id,)).fetchone()
        return dict(row) if row else None

    def get_slides_for_deck(self, deck_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM slide_chunks WHERE deck_chunk_id = ? ORDER BY slide_index",
            (deck_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_elements_for_slide(self, slide_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM element_chunks WHERE slide_chunk_id = ? ORDER BY position_in_slide",
            (slide_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_embeddings(self, table: str) -> list[tuple[str, np.ndarray]]:
        """Load all embeddings from a table for brute-force similarity search."""
        rows = self.conn.execute(
            f"SELECT id, embedding FROM {table} WHERE embedding IS NOT NULL"
        ).fetchall()
        return [(row["id"], np.frombuffer(row["embedding"], dtype=np.float32)) for row in rows]

    def fts_search(
        self,
        table: str,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        """Full-text search on an FTS5 table."""
        fts_table = f"{table}_fts"
        rows = self.conn.execute(
            f"""SELECT *, rank FROM {fts_table}
                WHERE {fts_table} MATCH ?
                ORDER BY rank LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Return index statistics."""
        stats = {}
        for table in ["deck_chunks", "slide_chunks", "element_chunks", "phrase_triggers"]:
            row = self.conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
            stats[table] = row["cnt"]
        return stats


# ── Helpers ────────────────────────────────────────────────────────


def _embed_to_blob(embedding: list[float]) -> bytes:
    return np.array(embedding, dtype=np.float32).tobytes()


def _blob_to_embed(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _normalize_phrase(phrase: str) -> str:
    """Lowercase, strip stopwords, normalize whitespace."""
    stopwords = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "about",
        "like",
        "through",
        "after",
        "over",
        "between",
        "out",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "my",
        "your",
        "our",
        "their",
        "me",
        "we",
        "you",
        "show",
        "make",
        "create",
        "build",
        "give",
        "how",
        "what",
    }
    words = phrase.lower().split()
    filtered = [w for w in words if w not in stopwords]
    return " ".join(filtered)


# ── Schema ─────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS deck_chunks (
    id TEXT PRIMARY KEY,
    source_file TEXT,
    title TEXT NOT NULL,
    author TEXT,
    company TEXT,
    created_at TEXT NOT NULL,
    narrative_summary TEXT DEFAULT '',
    audience TEXT DEFAULT '',
    purpose TEXT DEFAULT '',
    slide_count INTEGER DEFAULT 0,
    slide_type_sequence TEXT DEFAULT '[]',
    topic_tags TEXT DEFAULT '[]',
    template_used TEXT,
    brand_colors TEXT DEFAULT '[]',
    embedding BLOB
);

CREATE VIRTUAL TABLE IF NOT EXISTS deck_chunks_fts USING fts5(
    title, narrative_summary, audience, purpose, topic_tags,
    content='deck_chunks'
);

CREATE TABLE IF NOT EXISTS slide_chunks (
    id TEXT PRIMARY KEY,
    deck_chunk_id TEXT NOT NULL REFERENCES deck_chunks(id),
    slide_index INTEGER NOT NULL,
    slide_name TEXT,
    slide_type TEXT NOT NULL,
    layout_variant TEXT,
    background TEXT DEFAULT 'light',
    semantic_summary TEXT DEFAULT '',
    topic_tags TEXT DEFAULT '[]',
    content_domain TEXT DEFAULT '',
    has_stats INTEGER DEFAULT 0,
    stat_count INTEGER DEFAULT 0,
    has_bullets INTEGER DEFAULT 0,
    bullet_count INTEGER DEFAULT 0,
    has_columns INTEGER DEFAULT 0,
    column_count INTEGER DEFAULT 0,
    has_timeline INTEGER DEFAULT 0,
    step_count INTEGER DEFAULT 0,
    has_comparison INTEGER DEFAULT 0,
    has_image INTEGER DEFAULT 0,
    has_icons INTEGER DEFAULT 0,
    dsl_text TEXT DEFAULT '',
    thumbnail_path TEXT,
    color_palette TEXT DEFAULT '[]',
    prev_slide_type TEXT,
    next_slide_type TEXT,
    section_name TEXT,
    deck_position TEXT DEFAULT 'middle',
    use_count INTEGER DEFAULT 0,
    keep_count INTEGER DEFAULT 0,
    edit_count INTEGER DEFAULT 0,
    regen_count INTEGER DEFAULT 0,
    embedding BLOB
);

CREATE VIRTUAL TABLE IF NOT EXISTS slide_chunks_fts USING fts5(
    slide_name, semantic_summary, topic_tags, content_domain, dsl_text,
    content='slide_chunks'
);

CREATE TABLE IF NOT EXISTS element_chunks (
    id TEXT PRIMARY KEY,
    slide_chunk_id TEXT NOT NULL REFERENCES slide_chunks(id),
    deck_chunk_id TEXT NOT NULL REFERENCES deck_chunks(id),
    element_type TEXT NOT NULL,
    semantic_summary TEXT DEFAULT '',
    topic_tags TEXT DEFAULT '[]',
    raw_content TEXT DEFAULT '{}',
    visual_treatment TEXT DEFAULT '{}',
    slide_type TEXT,
    position_in_slide INTEGER DEFAULT 0,
    sibling_count INTEGER DEFAULT 0,
    embedding BLOB
);

CREATE VIRTUAL TABLE IF NOT EXISTS element_chunks_fts USING fts5(
    element_type, semantic_summary, topic_tags,
    content='element_chunks'
);

CREATE TABLE IF NOT EXISTS phrase_triggers (
    id TEXT PRIMARY KEY,
    phrase TEXT NOT NULL,
    normalized_phrase TEXT NOT NULL,
    matched_slide_chunk_id TEXT REFERENCES slide_chunks(id),
    matched_element_chunk_id TEXT REFERENCES element_chunks(id),
    confidence REAL DEFAULT 0.5,
    hit_count INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_phrase_normalized
    ON phrase_triggers(normalized_phrase);

CREATE TABLE IF NOT EXISTS feedback_log (
    id TEXT PRIMARY KEY,
    chunk_id TEXT NOT NULL,
    chunk_type TEXT NOT NULL,
    signal TEXT NOT NULL,
    context TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_chunk
    ON feedback_log(chunk_id);
"""
