"""
src/dsl/models.py — Pydantic data models for SlideDSL

These are the typed representations of the DSL grammar. Everything
flows through these models: parser produces them, serializer consumes
them, index chunks them, renderer reads them.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────


class SlideType(str, Enum):
    TITLE = "title"
    SECTION_DIVIDER = "section_divider"
    BULLET_POINTS = "bullet_points"
    TWO_COLUMN = "two_column"
    IMAGE_TEXT = "image_text"
    STAT_CALLOUT = "stat_callout"
    COMPARISON = "comparison"
    TIMELINE = "timeline"
    QUOTE = "quote"
    CLOSING = "closing"
    EXEC_SUMMARY = "exec_summary"
    NEXT_STEPS = "next_steps"
    FREEFORM = "freeform"


class BackgroundType(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    GRADIENT = "gradient"
    IMAGE = "image"


# ── Brand & Presentation Metadata ─────────────────────────────────


class BrandConfig(BaseModel):
    """Company brand settings — colors, fonts, logo."""

    primary: str = "1E2761"
    secondary: str = "CADCFC"
    accent: str = "F96167"
    header_font: str = "Arial Black"
    body_font: str = "Calibri"
    logo: Optional[str] = None


class PresentationMeta(BaseModel):
    """Presentation-level metadata from DSL frontmatter."""

    title: str = "Untitled Presentation"
    author: Optional[str] = None
    company: Optional[str] = None
    template: Optional[str] = None
    output: str = "pptx"
    date: Optional[str] = None
    confidentiality: Optional[str] = None  # "CONFIDENTIAL", "DRAFT", etc.
    brand: BrandConfig = Field(default_factory=BrandConfig)


# ── Content Elements ───────────────────────────────────────────────


class BulletItem(BaseModel):
    """A single bullet point, optionally with an icon."""

    text: str
    level: int = 0  # 0 = top-level, 1 = sub, 2 = sub-sub
    icon: Optional[str] = None


class StatItem(BaseModel):
    """A big-number stat callout."""

    value: str  # "94%", "3.2B", "$240K"
    label: str  # "Pipeline Uptime"
    description: Optional[str] = None  # "Up from 87% in Q2"


class TimelineStep(BaseModel):
    """A step in a timeline progression."""

    time: str  # "Jan 2025", "Q2 2025"
    title: str  # "Joined CMG"
    description: Optional[str] = None


class CompareTable(BaseModel):
    """A comparison/matrix table."""

    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class NextStepItem(BaseModel):
    """An action item for a next-steps slide."""

    action: str
    owner: Optional[str] = None
    timeline: Optional[str] = None


class ColumnContent(BaseModel):
    """Content for one column in a two_column slide."""

    title: Optional[str] = None
    bullets: list[BulletItem] = Field(default_factory=list)
    body: Optional[str] = None


# ── Slide ──────────────────────────────────────────────────────────


class SlideNode(BaseModel):
    """A single slide parsed from DSL."""

    slide_name: str
    slide_type: SlideType = SlideType.FREEFORM
    background: BackgroundType = BackgroundType.LIGHT
    layout: Optional[str] = None
    heading: Optional[str] = None
    subheading: Optional[str] = None
    body: Optional[str] = None
    bullets: list[BulletItem] = Field(default_factory=list)
    columns: list[ColumnContent] = Field(default_factory=list)
    stats: list[StatItem] = Field(default_factory=list)
    timeline: list[TimelineStep] = Field(default_factory=list)
    compare: Optional[CompareTable] = None
    speaker_notes: Optional[str] = None
    image: Optional[str] = None

    # Consulting slide metadata
    source: Optional[str] = None  # "Source: Company filings; BCG analysis"
    exhibit_label: Optional[str] = None  # "Exhibit 3: Revenue by segment ($M)"
    footnotes: list[str] = Field(default_factory=list)  # Numbered caveats

    # Next-steps items (for exec_summary / next_steps slides)
    next_steps: list[NextStepItem] = Field(default_factory=list)

    # Populated by design index at render time
    matched_design_id: Optional[str] = None
    matched_template_layout: Optional[str] = None


# ── Presentation ───────────────────────────────────────────────────


class PresentationNode(BaseModel):
    """Full parsed presentation = metadata + ordered slides."""

    meta: PresentationMeta
    slides: list[SlideNode]
