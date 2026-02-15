"""
agents/index_curator.py — Background Index Enrichment Agent (STUB)

TODO (Claude Code Phase 4):
    - Generate semantic summaries for deck/slide/element chunks
    - Extract topic tags and content domain classifications
    - Batch processing for cost efficiency (use Haiku)
    - Compute embeddings after enrichment

See specs/AGENT_SPEC.md for full contract.
See agents/prompts/index_curation.txt for system prompt.
"""

from dataclasses import dataclass

from src.dsl.models import PresentationNode, SlideNode


@dataclass
class DeckEnrichment:
    narrative_summary: str
    audience: str
    purpose: str
    topic_tags: list[str]


@dataclass
class SlideEnrichment:
    semantic_summary: str
    topic_tags: list[str]
    content_domain: str


@dataclass
class ElementEnrichment:
    semantic_summary: str
    topic_tags: list[str]


class IndexCuratorAgent:
    """Generates semantic metadata for design index chunks."""

    def enrich_deck(self, presentation: PresentationNode) -> DeckEnrichment:
        raise NotImplementedError("Index Curator not yet implemented.")

    def enrich_slide(self, slide: SlideNode, deck_context: str) -> SlideEnrichment:
        raise NotImplementedError("Index Curator not yet implemented.")

    def enrich_slides_batch(
        self, slides: list[SlideNode], deck_context: str
    ) -> list[SlideEnrichment]:
        raise NotImplementedError("Index Curator not yet implemented.")

    def enrich_element(self, element: dict, slide_context: str) -> ElementEnrichment:
        raise NotImplementedError("Index Curator not yet implemented.")
