"""
agents/index_curator.py — Background Index Enrichment Agent

Generates semantic metadata for design index chunks using Claude Haiku.
Runs asynchronously after deck ingestion to enrich chunks with:
  - Narrative summaries
  - Topic tags
  - Audience labels
  - Content domain classifications

Uses batching to minimize API calls (all slides from one deck in one call).

See specs/AGENT_SPEC.md for full contract.
See agents/prompts/index_curation.txt for system prompt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import anthropic

from src.dsl.models import PresentationNode, SlideNode
from src.dsl.serializer import SlideDSLSerializer

logger = logging.getLogger(__name__)

VALID_CONTENT_DOMAINS = frozenset(
    {
        "metrics",
        "strategy",
        "team",
        "risk",
        "roadmap",
        "overview",
        "financial",
        "technical",
        "comparison",
        "timeline",
        "closing",
    }
)


@dataclass
class DeckEnrichment:
    """Semantic metadata for a deck-level chunk."""

    narrative_summary: str
    audience: str
    purpose: str
    topic_tags: list[str]


@dataclass
class SlideEnrichment:
    """Semantic metadata for a slide-level chunk."""

    semantic_summary: str
    topic_tags: list[str]
    content_domain: str


@dataclass
class ElementEnrichment:
    """Semantic metadata for an element-level chunk."""

    semantic_summary: str
    topic_tags: list[str]


class IndexCuratorAgent:
    """
    Generates semantic metadata for design index chunks.

    Uses Claude Haiku for cost efficiency — this is a high-volume,
    relatively simple classification/summarization task.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        api_key: Optional[str] = None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.serializer = SlideDSLSerializer()
        self._system_prompt = self._load_system_prompt()

    def enrich_deck(self, presentation: PresentationNode) -> DeckEnrichment:
        """
        Generate semantic metadata for an entire deck.

        Args:
            presentation: The parsed presentation.

        Returns:
            DeckEnrichment with narrative summary, audience, purpose, tags.
        """
        dsl_text = self.serializer.serialize(presentation)
        # Truncate to avoid token limits
        dsl_text = dsl_text[:8000]

        prompt = (
            "Analyze this full presentation and produce deck-level metadata.\n\n"
            f"```\n{dsl_text}\n```\n\n"
            "Return a single JSON object with keys: "
            "narrative_summary, audience, purpose, topic_tags"
        )

        raw = self._call(prompt)
        data = _parse_json(raw)

        return DeckEnrichment(
            narrative_summary=data.get("narrative_summary", ""),
            audience=data.get("audience", ""),
            purpose=data.get("purpose", ""),
            topic_tags=data.get("topic_tags", []),
        )

    def enrich_slide(self, slide: SlideNode, deck_context: str) -> SlideEnrichment:
        """
        Generate semantic metadata for a single slide.

        Args:
            slide: The slide to enrich.
            deck_context: Brief description of the deck for context.

        Returns:
            SlideEnrichment with summary, tags, and content domain.
        """
        dsl_text = self.serializer.serialize_slide(slide)

        prompt = (
            f"Deck context: {deck_context}\n\n"
            f"Analyze this slide:\n```\n{dsl_text}\n```\n\n"
            "Return a single JSON object with keys: "
            "semantic_summary, topic_tags, content_domain"
        )

        raw = self._call(prompt)
        data = _parse_json(raw)

        return SlideEnrichment(
            semantic_summary=data.get("semantic_summary", ""),
            topic_tags=data.get("topic_tags", []),
            content_domain=_validate_domain(data.get("content_domain", "overview")),
        )

    def enrich_slides_batch(
        self, slides: list[SlideNode], deck_context: str
    ) -> list[SlideEnrichment]:
        """
        Enrich multiple slides in a single API call for efficiency.

        Args:
            slides: List of slides to enrich.
            deck_context: Brief deck description for context.

        Returns:
            List of SlideEnrichment, one per input slide.
        """
        if not slides:
            return []

        slide_texts: list[str] = []
        for i, slide in enumerate(slides):
            dsl_text = self.serializer.serialize_slide(slide)
            slide_texts.append(f"### Slide {i + 1}\n```\n{dsl_text}\n```")

        prompt = (
            f"Deck context: {deck_context}\n\n"
            "Analyze each slide below and return a JSON array with one object per slide.\n"
            "Each object must have keys: semantic_summary, topic_tags, content_domain\n\n"
            + "\n\n".join(slide_texts)
        )

        raw = self._call(prompt)
        data = _parse_json(raw)

        # Handle both array and single-object responses
        if isinstance(data, dict):
            data = [data]

        enrichments: list[SlideEnrichment] = []
        for i, slide in enumerate(slides):
            entry = data[i] if i < len(data) else {}
            enrichments.append(
                SlideEnrichment(
                    semantic_summary=entry.get("semantic_summary", ""),
                    topic_tags=entry.get("topic_tags", []),
                    content_domain=_validate_domain(entry.get("content_domain", "overview")),
                )
            )

        return enrichments

    def enrich_element(self, element: dict, slide_context: str) -> ElementEnrichment:
        """
        Generate semantic metadata for a single element.

        Args:
            element: Element data dict (e.g. stat, bullet, timeline step).
            slide_context: Brief description of the parent slide.

        Returns:
            ElementEnrichment with summary and tags.
        """
        element_text = json.dumps(element, indent=2, default=str)

        prompt = (
            f"Slide context: {slide_context}\n\n"
            f"Analyze this slide element:\n```json\n{element_text}\n```\n\n"
            "Return a single JSON object with keys: semantic_summary, topic_tags"
        )

        raw = self._call(prompt)
        data = _parse_json(raw)

        return ElementEnrichment(
            semantic_summary=data.get("semantic_summary", ""),
            topic_tags=data.get("topic_tags", []),
        )

    def enrich_elements_batch(
        self, elements: list[dict], slide_context: str
    ) -> list[ElementEnrichment]:
        """
        Enrich multiple elements in a single API call.

        Args:
            elements: List of element data dicts.
            slide_context: Brief description of the parent slide.

        Returns:
            List of ElementEnrichment, one per input element.
        """
        if not elements:
            return []

        element_texts: list[str] = []
        for i, elem in enumerate(elements):
            elem_json = json.dumps(elem, indent=2, default=str)
            element_texts.append(f"### Element {i + 1}\n```json\n{elem_json}\n```")

        prompt = (
            f"Slide context: {slide_context}\n\n"
            "Analyze each element below and return a JSON array with one object per element.\n"
            "Each object must have keys: semantic_summary, topic_tags\n\n"
            + "\n\n".join(element_texts)
        )

        raw = self._call(prompt)
        data = _parse_json(raw)

        if isinstance(data, dict):
            data = [data]

        enrichments: list[ElementEnrichment] = []
        for i in range(len(elements)):
            entry = data[i] if i < len(data) else {}
            enrichments.append(
                ElementEnrichment(
                    semantic_summary=entry.get("semantic_summary", ""),
                    topic_tags=entry.get("topic_tags", []),
                )
            )

        return enrichments

    # ── Internal ───────────────────────────────────────────────────

    def _load_system_prompt(self) -> str:
        """Load the index curation system prompt."""
        prompt_path = Path(__file__).parent / "prompts" / "index_curation.txt"
        return prompt_path.read_text(encoding="utf-8")

    def _call(self, prompt: str) -> str:
        """Make a single API call and return the text response."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=self._system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()


# ── Helpers ────────────────────────────────────────────────────────


def _parse_json(text: str) -> dict | list:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse curator JSON response: %s", text[:200])
        return {}


def _validate_domain(domain: str) -> str:
    """Ensure content_domain is one of the valid categories."""
    domain = domain.lower().strip()
    if domain in VALID_CONTENT_DOMAINS:
        return domain
    return "overview"
