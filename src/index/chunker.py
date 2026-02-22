"""
src/index/chunker.py — Multi-Granularity Slide Chunker

Chunks presentations at three levels:
  1. Deck-level  — narrative arc, audience, purpose, structure
  2. Slide-level — individual slide semantics, layout, content shape
  3. Element-level — specific charts, stats, visual treatments

Each chunk carries both structural metadata (deterministic, computed)
and semantic metadata (LLM-generated, via the Index Curator agent).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.dsl.models import (
    PresentationNode,
    SlideNode,
    SlideType,
)
from src.dsl.serializer import SlideForgeSerializer


# ═══════════════════════════════════════════════════════════════════════
# Chunk Data Structures
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class DeckChunk:
    """Deck-level chunk: the presentation as a whole."""

    id: str
    source_file: Optional[str]
    title: str
    author: Optional[str]
    company: Optional[str]
    created_at: str

    # Structural (computed deterministically)
    slide_count: int
    slide_type_sequence: list[str]
    topic_tags: list[str] = field(default_factory=list)
    template_used: Optional[str] = None
    brand_colors: list[str] = field(default_factory=list)

    # Presentation metadata
    date: Optional[str] = None
    confidentiality: Optional[str] = None

    # Semantic (populated by Index Curator agent)
    narrative_summary: str = ""
    audience: str = ""
    purpose: str = ""

    # Consulting quality (populated by Index Curator agent)
    storyline_quality: str = ""  # "good", "weak", "poor"
    consulting_style: str = ""  # "consulting", "corporate", "startup", "academic"

    # Embedding
    embedding: Optional[list[float]] = None

    # Child references
    slide_chunk_ids: list[str] = field(default_factory=list)

    def embedding_text(self) -> str:
        """Text representation for embedding generation."""
        parts = [self.title]
        if self.narrative_summary:
            parts.append(self.narrative_summary)
        if self.audience:
            parts.append(f"Audience: {self.audience}")
        if self.purpose:
            parts.append(f"Purpose: {self.purpose}")
        if self.topic_tags:
            parts.append(f"Topics: {', '.join(self.topic_tags)}")
        parts.append(f"Structure: {' → '.join(self.slide_type_sequence)}")
        if self.consulting_style:
            parts.append(f"Style: {self.consulting_style}")
        if self.storyline_quality:
            parts.append(f"Storyline: {self.storyline_quality}")
        return ". ".join(parts)


@dataclass
class SlideChunk:
    """Slide-level chunk: an individual slide's full context."""

    id: str
    deck_chunk_id: str
    slide_index: int

    # Identity
    slide_name: str
    slide_type: str
    layout_variant: Optional[str]
    background: str

    # Structural fingerprint (computed deterministically)
    has_stats: bool = False
    stat_count: int = 0
    has_bullets: bool = False
    bullet_count: int = 0
    has_columns: bool = False
    column_count: int = 0
    has_timeline: bool = False
    step_count: int = 0
    has_comparison: bool = False
    has_image: bool = False
    has_icons: bool = False
    has_source: bool = False
    has_exhibit: bool = False
    has_next_steps: bool = False
    next_step_count: int = 0

    # Content (the actual DSL text)
    dsl_text: str = ""

    # Neighborhood context
    prev_slide_type: Optional[str] = None
    next_slide_type: Optional[str] = None
    section_name: Optional[str] = None
    deck_position: str = "middle"  # "opening", "middle", "closing"

    # Semantic (populated by Index Curator agent)
    semantic_summary: str = ""
    topic_tags: list[str] = field(default_factory=list)
    content_domain: str = ""

    # Consulting quality (populated by Index Curator agent)
    action_title_quality: str = ""  # "good", "weak", "topic_label"

    # Visual metadata (populated after rendering)
    thumbnail_path: Optional[str] = None
    color_palette: list[str] = field(default_factory=list)

    # Quality signals
    use_count: int = 0
    keep_count: int = 0
    edit_count: int = 0
    regen_count: int = 0

    # Embedding
    embedding: Optional[list[float]] = None

    # Child references
    element_chunk_ids: list[str] = field(default_factory=list)

    def embedding_text(self) -> str:
        """Text representation for embedding generation."""
        parts = [self.slide_name]
        if self.semantic_summary:
            parts.append(self.semantic_summary)
        parts.append(f"Type: {self.slide_type}")
        if self.layout_variant:
            parts.append(f"Layout: {self.layout_variant}")
        if self.topic_tags:
            parts.append(f"Topics: {', '.join(self.topic_tags)}")

        # Structural shape description
        shape_parts = []
        if self.stat_count:
            shape_parts.append(f"{self.stat_count} stats")
        if self.bullet_count:
            shape_parts.append(f"{self.bullet_count} bullets")
        if self.column_count:
            shape_parts.append(f"{self.column_count} columns")
        if self.step_count:
            shape_parts.append(f"{self.step_count} timeline steps")
        if self.has_comparison:
            shape_parts.append("comparison table")
        if self.has_image:
            shape_parts.append("image")
        if shape_parts:
            parts.append(f"Contains: {', '.join(shape_parts)}")

        parts.append(f"Position: {self.deck_position}")
        if self.content_domain:
            parts.append(f"Domain: {self.content_domain}")
        if self.has_source:
            parts.append("Has source attribution")
        if self.has_next_steps:
            parts.append(f"{self.next_step_count} action items")
        return ". ".join(parts)

    @property
    def quality_score(self) -> float:
        """Ratio of keeps to total interactions. Higher = better design."""
        total = self.keep_count + self.regen_count
        if total == 0:
            return 0.5  # neutral for unused designs
        return self.keep_count / total


@dataclass
class ElementChunk:
    """Element-level chunk: a specific visual element within a slide."""

    id: str
    slide_chunk_id: str
    deck_chunk_id: str

    # Element identity
    element_type: str  # "stat", "bullet_group", "column", "timeline_step",
    # "comparison_row", "heading", "icon_bullet", "image"
    position_in_slide: int  # ordering within the slide
    sibling_count: int  # how many elements at this level

    # Raw content
    raw_content: dict  # type-specific content dict

    # Context
    slide_type: str  # parent slide's type

    # Semantic (populated by Index Curator)
    semantic_summary: str = ""
    topic_tags: list[str] = field(default_factory=list)

    # Visual treatment (populated after rendering)
    visual_treatment: dict = field(default_factory=dict)

    # Embedding
    embedding: Optional[list[float]] = None

    def embedding_text(self) -> str:
        """Text representation for embedding generation."""
        parts = [f"{self.element_type}"]
        if self.semantic_summary:
            parts.append(self.semantic_summary)
        if self.topic_tags:
            parts.append(f"Topics: {', '.join(self.topic_tags)}")
        parts.append(f"Content: {json.dumps(self.raw_content, default=str)[:200]}")
        parts.append(f"Context: {self.slide_type} slide")
        return ". ".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# Chunker
# ═══════════════════════════════════════════════════════════════════════


class SlideChunker:
    """
    Chunks a PresentationNode at three granularities.

    The chunker is deterministic — it computes structural metadata from
    the parsed DSL. Semantic metadata (summaries, tags) is populated
    separately by the Index Curator agent.
    """

    def __init__(self):
        self._serializer = SlideForgeSerializer()

    def chunk(
        self,
        presentation: PresentationNode,
        source_file: Optional[str] = None,
    ) -> tuple[DeckChunk, list[SlideChunk], list[ElementChunk]]:
        """
        Chunk a presentation at all three granularities.

        Returns:
            Tuple of (deck_chunk, slide_chunks, element_chunks).
            Semantic fields are empty — call the Index Curator to populate them.
        """
        deck_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # ── Deck chunk ─────────────────────────────────────────────

        slide_types = [s.slide_type.value for s in presentation.slides]
        brand = presentation.meta.brand
        brand_colors = [brand.primary, brand.secondary, brand.accent]

        deck_chunk = DeckChunk(
            id=deck_id,
            source_file=source_file,
            title=presentation.meta.title,
            author=presentation.meta.author,
            company=presentation.meta.company,
            created_at=now,
            slide_count=len(presentation.slides),
            slide_type_sequence=slide_types,
            template_used=presentation.meta.template,
            brand_colors=brand_colors,
            date=presentation.meta.date,
            confidentiality=presentation.meta.confidentiality,
        )

        # ── Slide + Element chunks ─────────────────────────────────

        slide_chunks: list[SlideChunk] = []
        element_chunks: list[ElementChunk] = []

        # Track sections for context
        current_section: Optional[str] = None

        for i, slide in enumerate(presentation.slides):
            slide_id = str(uuid.uuid4())

            # Update section tracking
            if slide.slide_type == SlideType.SECTION_DIVIDER:
                current_section = slide.heading or slide.slide_name

            # Determine deck position
            if i == 0:
                deck_position = "opening"
            elif i >= len(presentation.slides) - 1:
                deck_position = "closing"
            elif i <= 1:
                deck_position = "opening"
            elif i >= len(presentation.slides) - 2:
                deck_position = "closing"
            else:
                deck_position = "middle"

            # Structural fingerprint
            has_icons = any(b.icon for b in slide.bullets)

            slide_chunk = SlideChunk(
                id=slide_id,
                deck_chunk_id=deck_id,
                slide_index=i,
                slide_name=slide.slide_name,
                slide_type=slide.slide_type.value,
                layout_variant=slide.layout,
                background=slide.background.value,
                has_stats=len(slide.stats) > 0,
                stat_count=len(slide.stats),
                has_bullets=len(slide.bullets) > 0,
                bullet_count=len(slide.bullets),
                has_columns=len(slide.columns) > 0,
                column_count=len(slide.columns),
                has_timeline=len(slide.timeline) > 0,
                step_count=len(slide.timeline),
                has_comparison=slide.compare is not None,
                has_image=slide.image is not None,
                has_icons=has_icons,
                has_source=slide.source is not None,
                has_exhibit=slide.exhibit_label is not None,
                has_next_steps=len(slide.next_steps) > 0,
                next_step_count=len(slide.next_steps),
                dsl_text=self._serializer.serialize_slide(slide),
                prev_slide_type=(presentation.slides[i - 1].slide_type.value if i > 0 else None),
                next_slide_type=(
                    presentation.slides[i + 1].slide_type.value
                    if i < len(presentation.slides) - 1
                    else None
                ),
                section_name=current_section,
                deck_position=deck_position,
            )

            # ── Element chunks for this slide ──────────────────────

            slide_elements = self._chunk_elements(slide, slide_id, deck_id)
            slide_chunk.element_chunk_ids = [e.id for e in slide_elements]

            slide_chunks.append(slide_chunk)
            element_chunks.extend(slide_elements)
            deck_chunk.slide_chunk_ids.append(slide_id)

        return deck_chunk, slide_chunks, element_chunks

    def _chunk_elements(
        self,
        slide: SlideNode,
        slide_chunk_id: str,
        deck_chunk_id: str,
    ) -> list[ElementChunk]:
        """Extract element-level chunks from a slide."""
        elements: list[ElementChunk] = []
        position = 0

        # Heading as element
        if slide.heading:
            elements.append(
                ElementChunk(
                    id=str(uuid.uuid4()),
                    slide_chunk_id=slide_chunk_id,
                    deck_chunk_id=deck_chunk_id,
                    element_type="heading",
                    position_in_slide=position,
                    sibling_count=0,  # updated below
                    raw_content={
                        "heading": slide.heading,
                        "subheading": slide.subheading,
                    },
                    slide_type=slide.slide_type.value,
                )
            )
            position += 1

        # Each stat as a separate element
        for j, stat in enumerate(slide.stats):
            elements.append(
                ElementChunk(
                    id=str(uuid.uuid4()),
                    slide_chunk_id=slide_chunk_id,
                    deck_chunk_id=deck_chunk_id,
                    element_type="stat",
                    position_in_slide=position,
                    sibling_count=len(slide.stats),
                    raw_content={
                        "value": stat.value,
                        "label": stat.label,
                        "description": stat.description,
                        "index_in_group": j,
                        "group_size": len(slide.stats),
                    },
                    slide_type=slide.slide_type.value,
                )
            )
            position += 1

        # Bullets as a group element
        if slide.bullets:
            elements.append(
                ElementChunk(
                    id=str(uuid.uuid4()),
                    slide_chunk_id=slide_chunk_id,
                    deck_chunk_id=deck_chunk_id,
                    element_type="icon_bullet_group"
                    if any(b.icon for b in slide.bullets)
                    else "bullet_group",
                    position_in_slide=position,
                    sibling_count=len(slide.bullets),
                    raw_content={
                        "items": [
                            {"text": b.text, "level": b.level, "icon": b.icon}
                            for b in slide.bullets
                        ],
                        "has_icons": any(b.icon for b in slide.bullets),
                        "count": len(slide.bullets),
                    },
                    slide_type=slide.slide_type.value,
                )
            )
            position += 1

        # Each column as a separate element
        for j, col in enumerate(slide.columns):
            elements.append(
                ElementChunk(
                    id=str(uuid.uuid4()),
                    slide_chunk_id=slide_chunk_id,
                    deck_chunk_id=deck_chunk_id,
                    element_type="column",
                    position_in_slide=position,
                    sibling_count=len(slide.columns),
                    raw_content={
                        "title": col.title,
                        "bullets": [{"text": b.text, "level": b.level} for b in col.bullets],
                        "bullet_count": len(col.bullets),
                        "index_in_group": j,
                        "group_size": len(slide.columns),
                    },
                    slide_type=slide.slide_type.value,
                )
            )
            position += 1

        # Each timeline step as a separate element
        for j, step in enumerate(slide.timeline):
            elements.append(
                ElementChunk(
                    id=str(uuid.uuid4()),
                    slide_chunk_id=slide_chunk_id,
                    deck_chunk_id=deck_chunk_id,
                    element_type="timeline_step",
                    position_in_slide=position,
                    sibling_count=len(slide.timeline),
                    raw_content={
                        "time": step.time,
                        "title": step.title,
                        "description": step.description,
                        "index_in_group": j,
                        "group_size": len(slide.timeline),
                    },
                    slide_type=slide.slide_type.value,
                )
            )
            position += 1

        # Next-step action items as elements
        for j, ns in enumerate(slide.next_steps):
            elements.append(
                ElementChunk(
                    id=str(uuid.uuid4()),
                    slide_chunk_id=slide_chunk_id,
                    deck_chunk_id=deck_chunk_id,
                    element_type="action_item",
                    position_in_slide=position,
                    sibling_count=len(slide.next_steps),
                    raw_content={
                        "action": ns.action,
                        "owner": ns.owner,
                        "timeline": ns.timeline,
                        "index_in_group": j,
                        "group_size": len(slide.next_steps),
                    },
                    slide_type=slide.slide_type.value,
                )
            )
            position += 1

        # Comparison table rows as elements
        if slide.compare:
            for j, row in enumerate(slide.compare.rows):
                cells = (
                    dict(zip(slide.compare.headers, row))
                    if slide.compare.headers
                    else {"cells": row}
                )
                elements.append(
                    ElementChunk(
                        id=str(uuid.uuid4()),
                        slide_chunk_id=slide_chunk_id,
                        deck_chunk_id=deck_chunk_id,
                        element_type="comparison_row",
                        position_in_slide=position,
                        sibling_count=len(slide.compare.rows),
                        raw_content={
                            "headers": slide.compare.headers,
                            "row_data": cells,
                            "index_in_group": j,
                            "group_size": len(slide.compare.rows),
                        },
                        slide_type=slide.slide_type.value,
                    )
                )
                position += 1

        # Update sibling counts now that we know total
        total = len(elements)
        for e in elements:
            if e.sibling_count == 0:
                e.sibling_count = total

        return elements
