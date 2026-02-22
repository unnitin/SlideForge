"""
src/services/feedback.py — User Feedback → Index Updates

Processes user signals (keep, edit, regen) back into the design index
to improve future generations.
"""

from typing import Optional

from src.dsl.parser import SlideForgeParser
from src.index.chunker import SlideChunker
from src.index.store import DesignIndexStore


class FeedbackProcessor:
    """Processes user feedback signals into the design index."""

    def __init__(self, store: DesignIndexStore):
        self.store = store
        self.parser = SlideForgeParser()
        self.chunker = SlideChunker()

    def record_keep(self, slide_chunk_id: str):
        """User accepted the generated slide as-is."""
        self.store.record_feedback(slide_chunk_id, "slide", "keep")

    def record_edit(self, slide_chunk_id: str, edited_dsl: str):
        """User modified the slide then kept it."""
        self.store.record_feedback(
            slide_chunk_id,
            "slide",
            "edit",
            context={"edited_dsl": edited_dsl[:500]},
        )
        # Ingest the edited version as a new high-quality entry
        try:
            wrapper = f'---\npresentation:\n  title: "edited"\n---\n\n{edited_dsl}'
            pres = self.parser.parse(wrapper)
            if pres.slides:
                _, slide_chunks, element_chunks = self.chunker.chunk(pres)
                for sc in slide_chunks:
                    sc.keep_count = 1  # starts with positive signal
                    self.store.upsert_slide(sc)
                for ec in element_chunks:
                    self.store.upsert_element(ec)
        except Exception:
            pass  # don't fail on feedback processing

    def record_regen(self, slide_chunk_id: str):
        """User rejected and asked for regeneration."""
        self.store.record_feedback(slide_chunk_id, "slide", "regen")

    def record_phrase_hit(
        self,
        phrase: str,
        slide_chunk_id: Optional[str] = None,
        element_chunk_id: Optional[str] = None,
    ):
        """Record that a natural language phrase matched a design."""
        self.store.record_phrase_trigger(phrase, slide_chunk_id, element_chunk_id)
