"""
src/renderer/pptx_renderer.py -- PPTX Rendering Engine

Converts a PresentationNode into a .pptx file using python-pptx.
Deterministic: same input always produces the same output.

Two modes:
  1. Template-based: maps slides to existing template layouts
  2. Brand-based: generates slides from scratch using BrandConfig
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from src.dsl.models import (
    BackgroundType,
    BrandConfig,
    BulletItem,
    PresentationMeta,
    PresentationNode,
    SlideNode,
    SlideType,
)

logger = logging.getLogger(__name__)

# ── Geometry Constants (inches, 16:9) ─────────────────────────────

SLIDE_WIDTH = Inches(13.333)
SLIDE_HEIGHT = Inches(7.5)

MARGIN_TOP = 0.6
MARGIN_BOTTOM = 0.6  # Symmetric with top
MARGIN_LEFT = 0.7
MARGIN_RIGHT = 0.7

CONTENT_WIDTH = 13.333 - MARGIN_LEFT - MARGIN_RIGHT  # 11.933
CONTENT_HEIGHT = 7.5 - MARGIN_TOP - MARGIN_BOTTOM  # 6.3
CONTENT_TOP = MARGIN_TOP + 1.3  # 1.9 — below title (1.6) + separator + exhibit label

# Source / footnote zone at bottom of slide
SOURCE_FONT = 8  # consulting source/footnote lines are 7–8pt
SOURCE_TOP = 7.5 - MARGIN_BOTTOM - 0.35  # ~6.55 — source + page number row
CONF_TOP = SOURCE_TOP - 0.22  # confidentiality row sits above source
FOOTER_RULE_TOP = CONF_TOP - 0.08  # hairline sits above confidentiality row
SOURCE_HEIGHT = 0.25
PAGE_NUM_WIDTH = 0.5

TITLE_LEFT = MARGIN_LEFT
TITLE_TOP = MARGIN_TOP
TITLE_WIDTH = CONTENT_WIDTH
TITLE_HEIGHT = 0.85  # fits a 2-line 20pt action title with minimal dead space below

# Body content zone — starts after title + separator gap
BODY_HEIGHT = 4.0  # available height before footnotes/source zone

# Font sizes (points) — calibrated against Bain/BCG consulting decks
FONT_TITLE = 32  # cover/section titles: 28–36pt in consulting
FONT_SUBTITLE = 18  # subtitle / governing-thought on title and section slides
FONT_HEADING = 20  # action title on content slides: 18–22pt
FONT_BODY = 11  # consulting body text is consistently 9–11pt
FONT_CAPTION = 9  # eyebrow labels, exhibit captions: 8–10pt
FONT_STAT_VALUE = 44  # inline stat callouts: 36–48pt
FONT_STAT_LABEL = 13  # stat label (bold, below value): clearly subordinate
FONT_STAT_DESC = 10  # stat descriptor: 9–10pt, never competes with value

# Spacing
ELEMENT_GAP = 0.3
COLUMN_GAP = 0.4


# ── Color Utilities ───────────────────────────────────────────────


def resolve_color(color_ref: str, brand: BrandConfig) -> RGBColor:
    """Resolve a color reference to an RGBColor."""
    named = {"white": "FFFFFF", "black": "000000"}
    brand_map = {
        "primary": brand.primary,
        "secondary": brand.secondary,
        "accent": brand.accent,
    }
    hex_val = brand_map.get(color_ref) or named.get(color_ref) or color_ref
    r = int(hex_val[0:2], 16)
    g = int(hex_val[2:4], 16)
    b = int(hex_val[4:6], 16)
    return RGBColor(r, g, b)


def _text_color_for_bg(bg: BackgroundType, brand: BrandConfig) -> RGBColor:
    """Return appropriate text color for a background type."""
    if bg in (BackgroundType.DARK, BackgroundType.GRADIENT):
        return RGBColor(0xFF, 0xFF, 0xFF)
    return RGBColor(0x1A, 0x1A, 0x1A)  # near-black; consulting decks avoid pure #000


def _muted_color_for_bg(bg: BackgroundType, brand: BrandConfig) -> RGBColor:
    """Return a muted/secondary text color for a background type."""
    if bg in (BackgroundType.DARK, BackgroundType.GRADIENT):
        return RGBColor(0xCC, 0xCC, 0xCC)
    return RGBColor(0x76, 0x76, 0x76)  # #767676 — source notes / axis labels


# ── Markdown Run Renderer ─────────────────────────────────────────

_MD_INLINE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*", re.DOTALL)


def _add_paragraph_runs(
    paragraph,
    text: str,
    font_size: int,
    bold: bool = False,
    color: Optional[RGBColor] = None,
    font_name: Optional[str] = None,
) -> None:
    """Set paragraph text, converting **bold** and *italic* markdown into runs.

    Falls back to a direct paragraph.text assignment when no markdown is present
    so that paragraph-level font properties (size, bold, color) still work for
    the common case and tests that inspect paragraph.font continue to pass.
    """
    if "**" not in text and "*" not in text:
        paragraph.text = text
        paragraph.font.size = Pt(font_size)
        paragraph.font.bold = bold
        if color:
            paragraph.font.color.rgb = color
        if font_name:
            paragraph.font.name = font_name
        return

    # Build (kind, chunk) segments
    segments: list[tuple[str, str]] = []
    last_end = 0
    for m in _MD_INLINE.finditer(text):
        if m.start() > last_end:
            segments.append(("plain", text[last_end : m.start()]))
        if m.group(1) is not None:
            segments.append(("bold", m.group(1)))
        else:
            segments.append(("italic", m.group(2)))
        last_end = m.end()
    if last_end < len(text):
        segments.append(("plain", text[last_end:]))

    for kind, chunk in segments:
        run = paragraph.add_run()
        run.text = chunk
        run.font.size = Pt(font_size)
        if kind == "bold":
            run.font.bold = True
        elif kind == "italic":
            run.font.italic = True
            run.font.bold = bold
        else:
            run.font.bold = bold
        if color:
            run.font.color.rgb = color
        if font_name:
            run.font.name = font_name


# ── Font Scaling ──────────────────────────────────────────────────


def _stat_value_size(value: str) -> int:
    """Return a font size for a stat value that avoids box overflow.

    Stat values should be short (e.g. "$180M", "40%", "3x").  When the model
    includes extra words (e.g. "3–5 Years"), scale down to prevent the text
    from wrapping out of the value textbox and into the label below.
    """
    n = len(value)
    if n > 8:
        return max(FONT_STAT_VALUE - 12, 24)  # 32pt for long values
    if n > 5:
        return max(FONT_STAT_VALUE - 6, 24)  # 38pt for medium values
    return FONT_STAT_VALUE  # 44pt for short values like "$2B"


def _heading_size(text: str) -> int:
    """Return a font size for a slide heading that avoids overflow.

    Consulting action titles can be 60–120 chars.  At 20pt Arial Black on a
    11.9" content width, each line holds roughly 70–75 chars.  We scale down
    for long headings so they still fit inside TITLE_HEIGHT (1.0").
    """
    n = len(text)
    if n > 110:
        return max(FONT_HEADING - 4, 14)  # 16pt — fits ~3 lines
    if n > 75:
        return max(FONT_HEADING - 2, 14)  # 18pt — fits ~2 lines
    return FONT_HEADING  # 20pt — fits ~1–2 lines comfortably


# ── Background ────────────────────────────────────────────────────


def _apply_background(slide, bg_type: BackgroundType, brand: BrandConfig):
    """Apply background fill to a slide."""
    background = slide.background
    fill = background.fill
    fill.solid()

    if bg_type == BackgroundType.DARK:
        fill.fore_color.rgb = resolve_color("primary", brand)
    elif bg_type == BackgroundType.GRADIENT:
        fill.fore_color.rgb = resolve_color("primary", brand)
    elif bg_type == BackgroundType.LIGHT:
        fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    else:
        fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)


# ── Text Helpers ──────────────────────────────────────────────────


def _add_textbox(
    slide,
    left: float,
    top: float,
    width: float,
    height: float,
    text: str,
    font_size: int = FONT_BODY,
    bold: bool = False,
    color: Optional[RGBColor] = None,
    alignment: PP_ALIGN = PP_ALIGN.LEFT,
    font_name: Optional[str] = None,
) -> object:
    """Add a textbox to a slide and return the shape."""
    txBox = slide.shapes.add_textbox(
        Inches(left),
        Inches(top),
        Inches(width),
        Inches(height),
    )
    tf = txBox.text_frame
    tf.word_wrap = True

    p = tf.paragraphs[0]
    p.alignment = alignment
    _add_paragraph_runs(p, text, font_size, bold=bold, color=color, font_name=font_name)
    return txBox


def _add_bullet_list(
    slide,
    left: float,
    top: float,
    width: float,
    height: float,
    bullets: list[BulletItem],
    font_size: int = FONT_BODY,
    color: Optional[RGBColor] = None,
    font_name: Optional[str] = None,
) -> object:
    """Add a bulleted list to a slide."""
    txBox = slide.shapes.add_textbox(
        Inches(left),
        Inches(top),
        Inches(width),
        Inches(height),
    )
    tf = txBox.text_frame
    tf.word_wrap = True

    for i, bullet in enumerate(bullets):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
            p.space_before = Pt(8)  # breathing room between bullets

        bullet_font_size = font_size - (bullet.level * 2)
        p.level = bullet.level
        _add_paragraph_runs(p, bullet.text, bullet_font_size, color=color, font_name=font_name)

    return txBox


# ── Visual Helper Functions ───────────────────────────────────────


def _render_content_separator(slide, brand: BrandConfig):
    """Render thin horizontal rule below slide title area.

    Draws a hairline (~0.5pt / 0.03") at TITLE_TOP + TITLE_HEIGHT + 0.05".
    Uses a fixed light gray (#CCCCCC) matching the consulting standard —
    never a brand color, which would make the rule visually compete with content.
    """
    sep = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(TITLE_LEFT),
        Inches(TITLE_TOP + TITLE_HEIGHT + 0.05),
        Inches(TITLE_WIDTH),
        Inches(0.03),
    )
    sep.fill.solid()
    sep.fill.fore_color.rgb = RGBColor(0xCC, 0xCC, 0xCC)  # fixed hairline gray
    sep.line.fill.background()


def _render_stat_card_bg(
    slide,
    x: float,
    y: float,
    width: float,
    height: float,
    brand: BrandConfig,
    bg_type: BackgroundType,
):
    """Render a card background rectangle behind a stat column.

    Light backgrounds get a soft grey card; dark/gradient backgrounds
    get a slightly lighter variant of the primary color.
    """
    if bg_type in (BackgroundType.DARK, BackgroundType.GRADIENT):
        card_color = RGBColor(0x2E, 0x3A, 0x80)
    else:
        card_color = RGBColor(0xF5, 0xF7, 0xFA)

    card = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(x),
        Inches(y),
        Inches(width),
        Inches(height),
    )
    card.fill.solid()
    card.fill.fore_color.rgb = card_color
    card.line.fill.background()


def _render_logo(
    slide,
    brand: BrandConfig,
    x: float,
    y: float,
    width: float = 1.0,
    height: float = 0.4,
):
    """Render the company logo on a slide.

    Silently skips if brand.logo is None or the file is not found.
    """
    if not brand.logo:
        return
    try:
        slide.shapes.add_picture(
            brand.logo,
            Inches(x),
            Inches(y),
            Inches(width),
            Inches(height),
        )
    except Exception:
        logger.debug("Logo file not found or invalid: %s", brand.logo)


def _render_footer_rule(slide, brand: BrandConfig):
    """Render a hairline rule above the footer zone.

    Both Bain and BCG use a thin gray rule separating the content body from
    the bottom metadata zone (confidentiality, source, footnotes, page number).
    """
    rule = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(MARGIN_LEFT),
        Inches(FOOTER_RULE_TOP),
        Inches(CONTENT_WIDTH),
        Inches(0.02),
    )
    rule.fill.solid()
    rule.fill.fore_color.rgb = RGBColor(0xDD, 0xDD, 0xDD)
    rule.line.fill.background()


def _render_confidentiality_label(slide, meta: PresentationMeta, brand: BrandConfig):
    """Render confidentiality label centered at the bottom of a slide.

    Placed on CONF_TOP (above the source/page-number row) so it never
    overlaps with left-aligned source text or the right-aligned page number.
    """
    muted = _muted_color_for_bg(BackgroundType.LIGHT, brand)
    _add_textbox(
        slide,
        MARGIN_LEFT,
        CONF_TOP,
        CONTENT_WIDTH,
        0.2,
        meta.confidentiality,
        font_size=8,
        color=muted,
        alignment=PP_ALIGN.CENTER,
        font_name=brand.body_font,
    )


def _render_title_accent_band(slide, brand: BrandConfig, bg_type: BackgroundType):
    """Render a full-width accent band at the bottom of the title slide.

    Uses the accent color for dark/gradient backgrounds, brand primary
    for light backgrounds — visually grounds the title slide.
    """
    if bg_type in (BackgroundType.DARK, BackgroundType.GRADIENT):
        band_color = resolve_color("accent", brand)
    else:
        band_color = resolve_color("primary", brand)

    band = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(0),
        Inches(6.6),
        Inches(13.333),
        Inches(0.9),
    )
    band.fill.solid()
    band.fill.fore_color.rgb = band_color
    band.line.fill.background()


# ── Per-Type Renderers ────────────────────────────────────────────


def _render_title(slide, node: SlideNode, brand: BrandConfig):
    """Render a title slide."""
    text_color = _text_color_for_bg(node.background, brand)
    muted = _muted_color_for_bg(node.background, brand)

    if node.heading:
        _add_textbox(
            slide,
            MARGIN_LEFT,
            2.0,
            CONTENT_WIDTH,
            1.5,
            node.heading,
            font_size=FONT_TITLE,  # 32pt; cover titles are 28–36pt in consulting
            bold=True,
            color=text_color,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.header_font,
        )

    if node.subheading:
        _add_textbox(
            slide,
            MARGIN_LEFT,
            3.8,
            CONTENT_WIDTH,
            1.0,
            node.subheading,
            font_size=FONT_SUBTITLE,
            color=muted,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.body_font,
        )

    _render_title_accent_band(slide, brand, node.background)
    _render_logo(slide, brand, x=11.9, y=6.85)


def _render_section_divider(slide, node: SlideNode, brand: BrandConfig):
    """Render a section divider slide."""
    text_color = _text_color_for_bg(node.background, brand)
    muted = _muted_color_for_bg(node.background, brand)

    if node.heading:
        _add_textbox(
            slide,
            MARGIN_LEFT,
            2.2,
            CONTENT_WIDTH,
            1.3,
            node.heading,
            font_size=FONT_TITLE,
            bold=True,
            color=text_color,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.header_font,
        )

    # Accent strip
    accent = resolve_color("accent", brand)
    strip_y = 3.6 if node.subheading else 4.0
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(MARGIN_LEFT + 3.0),
        Inches(strip_y),
        Inches(CONTENT_WIDTH - 6.0),
        Inches(0.06),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = accent
    shape.line.fill.background()

    # Governing thought / subtitle — shown below the accent strip
    if node.subheading:
        _add_textbox(
            slide,
            MARGIN_LEFT + 1.5,
            strip_y + 0.2,
            CONTENT_WIDTH - 3.0,
            0.7,
            node.subheading,
            font_size=FONT_SUBTITLE,
            color=muted,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.body_font,
        )


def _render_stat_callout(slide, node: SlideNode, brand: BrandConfig):
    """Render a stat callout slide with big numbers."""
    text_color = _text_color_for_bg(node.background, brand)
    muted = _muted_color_for_bg(node.background, brand)
    accent = resolve_color("accent", brand)

    # Heading
    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    _render_content_separator(slide, brand)

    if not node.stats:
        return

    stat_count = len(node.stats)
    stat_width = CONTENT_WIDTH / stat_count
    stat_top = CONTENT_TOP + 0.4

    for i, stat in enumerate(node.stats):
        x = MARGIN_LEFT + i * stat_width

        # Card background (drawn first so it sits below textboxes)
        # Height 3.0" gives room for value (1.4") + label (0.5") + desc (0.6") + padding
        _render_stat_card_bg(
            slide, x + 0.1, stat_top - 0.3, stat_width - 0.2, 3.0, brand, node.background
        )

        # Value — auto-scaled font prevents long values from overflowing 1.4" box
        _add_textbox(
            slide,
            x,
            stat_top,
            stat_width,
            1.4,
            stat.value,
            font_size=_stat_value_size(stat.value),
            bold=True,
            color=accent,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.header_font,
        )

        # Label — positioned below value box (stat_top + 1.4 + 0.2 gap)
        _add_textbox(
            slide,
            x,
            stat_top + 1.6,
            stat_width,
            0.5,
            stat.label,
            font_size=FONT_STAT_LABEL,
            bold=True,
            color=text_color,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.body_font,
        )

        # Description — below label
        if stat.description:
            _add_textbox(
                slide,
                x,
                stat_top + 2.2,
                stat_width,
                0.6,
                stat.description,
                font_size=FONT_STAT_DESC,
                color=muted,
                alignment=PP_ALIGN.CENTER,
                font_name=brand.body_font,
            )


def _render_bullet_points(slide, node: SlideNode, brand: BrandConfig):
    """Render a bullet points slide."""
    text_color = _text_color_for_bg(node.background, brand)

    # Heading
    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    _render_content_separator(slide, brand)

    if node.bullets:
        if node.layout == "icon_rows":
            _render_icon_rows(slide, node, brand)
        else:
            _add_bullet_list(
                slide,
                MARGIN_LEFT,
                CONTENT_TOP,
                CONTENT_WIDTH,
                BODY_HEIGHT,
                node.bullets,
                font_size=FONT_BODY,
                color=text_color,
                font_name=brand.body_font,
            )


def _render_icon_rows(slide, node: SlideNode, brand: BrandConfig):
    """Render bullet points as icon rows layout with accent circles."""
    text_color = _text_color_for_bg(node.background, brand)
    accent = resolve_color("accent", brand)
    n = len(node.bullets)
    # Spread rows evenly like exec_summary; cap at 1.0" so sparse slides
    # don't look floaty.
    row_height = min(BODY_HEIGHT / max(n, 1), 1.0)
    start_top = CONTENT_TOP + 0.1

    for i, bullet in enumerate(node.bullets):
        y = start_top + i * row_height

        # Icon circle
        shape = slide.shapes.add_shape(
            9,  # MSO_SHAPE.OVAL
            Inches(MARGIN_LEFT),
            Inches(y + 0.04),
            Inches(0.42),
            Inches(0.42),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = accent
        shape.line.fill.background()

        # Icon label inside circle — first char of icon name or bullet number
        tf = shape.text_frame
        label = bullet.icon[0].upper() if bullet.icon else str(i + 1)
        tf.paragraphs[0].text = label
        tf.paragraphs[0].font.size = Pt(13)
        tf.paragraphs[0].font.bold = True
        tf.paragraphs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        tf.word_wrap = False

        # Text — height sized to slot so wrapping text isn't clipped
        _add_textbox(
            slide,
            MARGIN_LEFT + 0.60,
            y,
            CONTENT_WIDTH - 0.60,
            max(row_height - 0.08, 0.45),
            bullet.text,
            font_size=FONT_BODY + 1,
            color=text_color,
            font_name=brand.body_font,
        )


def _render_two_column(slide, node: SlideNode, brand: BrandConfig):
    """Render a two-column slide."""
    text_color = _text_color_for_bg(node.background, brand)

    # Heading
    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    _render_content_separator(slide, brand)

    col_width = (CONTENT_WIDTH - COLUMN_GAP) / 2

    for i, col in enumerate(node.columns[:2]):
        x = MARGIN_LEFT + i * (col_width + COLUMN_GAP)
        y = CONTENT_TOP

        # Column title
        if col.title:
            _add_textbox(
                slide,
                x,
                y,
                col_width,
                0.5,
                col.title,
                font_size=FONT_BODY + 2,
                bold=True,
                color=text_color,
                font_name=brand.header_font,
            )
            y += 0.6

        # Column bullets
        if col.bullets:
            _add_bullet_list(
                slide,
                x,
                y,
                col_width,
                BODY_HEIGHT,
                col.bullets,
                font_size=FONT_BODY,
                color=text_color,
                font_name=brand.body_font,
            )

    # Divider line
    if len(node.columns) >= 2:
        center_x = MARGIN_LEFT + col_width + COLUMN_GAP / 2
        shape = slide.shapes.add_shape(
            1,  # MSO_SHAPE.RECTANGLE
            Inches(center_x - 0.01),
            Inches(CONTENT_TOP),
            Inches(0.02),
            Inches(4.5),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(0xDD, 0xDD, 0xDD)
        shape.line.fill.background()


def _render_comparison(slide, node: SlideNode, brand: BrandConfig):
    """Render a comparison table slide."""
    text_color = _text_color_for_bg(node.background, brand)

    # Heading
    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    _render_content_separator(slide, brand)

    if not node.compare:
        return

    headers = node.compare.headers
    rows = node.compare.rows
    col_count = len(headers) if headers else (len(rows[0]) if rows else 0)
    if col_count == 0:
        return

    row_count = len(rows) + (1 if headers else 0)
    table_top = CONTENT_TOP + 0.2
    # Cap at 4.0" so table bottom (table_top + 4.0 = 2.1 + 4.0 = 6.1") stays
    # above the footnote zone which starts at SOURCE_TOP - 0.35 ≈ 6.15"
    table_height = min(row_count * 0.6, 4.0)

    table_shape = slide.shapes.add_table(
        row_count,
        col_count,
        Inches(MARGIN_LEFT),
        Inches(table_top),
        Inches(CONTENT_WIDTH),
        Inches(table_height),
    )
    table = table_shape.table

    # Header row
    if headers:
        for j, hdr in enumerate(headers):
            cell = table.cell(0, j)
            cell.text = hdr
            p = cell.text_frame.paragraphs[0]
            p.font.bold = True
            p.font.size = Pt(FONT_BODY)
            p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            p.font.name = brand.header_font
            cell.fill.solid()
            cell.fill.fore_color.rgb = resolve_color("primary", brand)

    # Data rows
    row_offset = 1 if headers else 0
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            if j >= col_count:
                break
            cell = table.cell(i + row_offset, j)
            cell.text = val
            p = cell.text_frame.paragraphs[0]
            p.font.size = Pt(FONT_BODY - 1)
            p.font.name = brand.body_font
            # Alternating row colors
            if i % 2 == 1:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(0xF5, 0xF5, 0xF5)


def _render_timeline(slide, node: SlideNode, brand: BrandConfig):
    """Render a timeline slide with horizontal steps."""
    text_color = _text_color_for_bg(node.background, brand)
    accent = resolve_color("accent", brand)
    muted = _muted_color_for_bg(node.background, brand)

    # Heading
    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    _render_content_separator(slide, brand)

    if not node.timeline:
        return

    step_count = len(node.timeline)
    step_width = CONTENT_WIDTH / step_count
    line_y = CONTENT_TOP + 1.0

    # Connecting line
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(MARGIN_LEFT + 0.3),
        Inches(line_y + 0.18),
        Inches(CONTENT_WIDTH - 0.6),
        Inches(0.04),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = resolve_color("secondary", brand)
    shape.line.fill.background()

    for i, step in enumerate(node.timeline):
        x = MARGIN_LEFT + i * step_width + step_width / 2

        # Circle marker
        circle = slide.shapes.add_shape(
            9,  # MSO_SHAPE.OVAL
            Inches(x - 0.2),
            Inches(line_y),
            Inches(0.4),
            Inches(0.4),
        )
        circle.fill.solid()
        circle.fill.fore_color.rgb = accent
        circle.line.fill.background()

        # Step number
        tf = circle.text_frame
        tf.paragraphs[0].text = str(i + 1)
        tf.paragraphs[0].font.size = Pt(12)
        tf.paragraphs[0].font.bold = True
        tf.paragraphs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER

        # Time label
        _add_textbox(
            slide,
            x - step_width / 2,
            line_y - 0.6,
            step_width,
            0.4,
            step.time,
            font_size=FONT_CAPTION,
            bold=True,
            color=accent,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.body_font,
        )

        # Title
        _add_textbox(
            slide,
            x - step_width / 2,
            line_y + 0.6,
            step_width,
            0.4,
            step.title,
            font_size=FONT_BODY - 1,
            bold=True,
            color=text_color,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.body_font,
        )

        # Description
        if step.description:
            _add_textbox(
                slide,
                x - step_width / 2,
                line_y + 1.1,
                step_width,
                0.5,
                step.description,
                font_size=FONT_CAPTION,
                color=muted,
                alignment=PP_ALIGN.CENTER,
                font_name=brand.body_font,
            )


def _render_image_text(slide, node: SlideNode, brand: BrandConfig):
    """Render an image + text slide."""
    text_color = _text_color_for_bg(node.background, brand)

    # Heading
    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    # Image placeholder (left side)
    if node.image:
        try:
            slide.shapes.add_picture(
                node.image,
                Inches(MARGIN_LEFT),
                Inches(CONTENT_TOP),
                Inches(CONTENT_WIDTH / 2 - COLUMN_GAP / 2),
                Inches(BODY_HEIGHT),
            )
        except Exception:
            logger.warning("Image not found: %s, using placeholder", node.image)
            _add_textbox(
                slide,
                MARGIN_LEFT,
                CONTENT_TOP + 1.5,
                CONTENT_WIDTH / 2 - COLUMN_GAP / 2,
                1.0,
                f"[Image: {node.image}]",
                font_size=FONT_CAPTION,
                color=_muted_color_for_bg(node.background, brand),
                alignment=PP_ALIGN.CENTER,
            )

    # Text (right side)
    text_x = MARGIN_LEFT + CONTENT_WIDTH / 2 + COLUMN_GAP / 2
    text_width = CONTENT_WIDTH / 2 - COLUMN_GAP / 2

    if node.bullets:
        _add_bullet_list(
            slide,
            text_x,
            CONTENT_TOP,
            text_width,
            BODY_HEIGHT,
            node.bullets,
            font_size=FONT_BODY,
            color=text_color,
            font_name=brand.body_font,
        )
    elif node.body:
        _add_textbox(
            slide,
            text_x,
            CONTENT_TOP,
            text_width,
            BODY_HEIGHT,
            node.body,
            font_size=FONT_BODY,
            color=text_color,
            font_name=brand.body_font,
        )


def _render_quote(slide, node: SlideNode, brand: BrandConfig):
    """Render a quote slide."""
    text_color = _text_color_for_bg(node.background, brand)
    accent = resolve_color("accent", brand)
    muted = _muted_color_for_bg(node.background, brand)

    # Large quotation mark
    _add_textbox(
        slide,
        MARGIN_LEFT + 1.0,
        1.5,
        1.5,
        1.5,
        "\u201c",
        font_size=96,
        color=accent,
        font_name=brand.header_font,
    )

    # Quote text
    if node.heading:
        _add_textbox(
            slide,
            MARGIN_LEFT + 1.5,
            2.5,
            CONTENT_WIDTH - 3.0,
            2.5,
            node.heading,
            font_size=24,
            color=text_color,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.body_font,
        )

    # Attribution
    if node.subheading:
        _add_textbox(
            slide,
            MARGIN_LEFT + 1.5,
            5.0,
            CONTENT_WIDTH - 3.0,
            0.5,
            f"\u2014 {node.subheading}",
            font_size=FONT_BODY,
            color=muted,
            alignment=PP_ALIGN.RIGHT,
            font_name=brand.body_font,
        )


def _render_closing(slide, node: SlideNode, brand: BrandConfig):
    """Render a closing slide."""
    text_color = _text_color_for_bg(node.background, brand)
    muted = _muted_color_for_bg(node.background, brand)

    if node.heading:
        _add_textbox(
            slide,
            MARGIN_LEFT,
            2.5,
            CONTENT_WIDTH,
            1.5,
            node.heading,
            font_size=FONT_TITLE,
            bold=True,
            color=text_color,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.header_font,
        )

    if node.subheading:
        _add_textbox(
            slide,
            MARGIN_LEFT,
            4.2,
            CONTENT_WIDTH,
            0.8,
            node.subheading,
            font_size=FONT_SUBTITLE,
            color=muted,
            alignment=PP_ALIGN.CENTER,
            font_name=brand.body_font,
        )

    _render_logo(slide, brand, x=11.9, y=6.85)


def _render_freeform(slide, node: SlideNode, brand: BrandConfig):
    """Render a freeform slide -- best effort based on available content."""
    text_color = _text_color_for_bg(node.background, brand)

    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    _render_content_separator(slide, brand)

    if node.bullets:
        _add_bullet_list(
            slide,
            MARGIN_LEFT,
            CONTENT_TOP,
            CONTENT_WIDTH,
            BODY_HEIGHT,
            node.bullets,
            font_size=FONT_BODY,
            color=text_color,
            font_name=brand.body_font,
        )
    elif node.body:
        _add_textbox(
            slide,
            MARGIN_LEFT,
            CONTENT_TOP,
            CONTENT_WIDTH,
            BODY_HEIGHT,
            node.body,
            font_size=FONT_BODY,
            color=text_color,
            font_name=brand.body_font,
        )


# ── Consulting Metadata Renderers ─────────────────────────────────


def _render_source_line(slide, node: SlideNode, brand: BrandConfig):
    """Render the source attribution line at the bottom of a slide."""
    if not node.source:
        return
    muted = _muted_color_for_bg(node.background, brand)
    _add_textbox(
        slide,
        MARGIN_LEFT,
        SOURCE_TOP,
        CONTENT_WIDTH - PAGE_NUM_WIDTH - 0.2,
        SOURCE_HEIGHT,
        node.source,
        font_size=SOURCE_FONT,
        color=muted,
        alignment=PP_ALIGN.LEFT,
        font_name=brand.body_font,
    )


def _render_exhibit_label(slide, node: SlideNode, brand: BrandConfig):
    """Render the exhibit label between the separator and body content."""
    if not node.exhibit_label:
        return
    muted = _muted_color_for_bg(node.background, brand)
    # Position below the separator line (TITLE_TOP + TITLE_HEIGHT + 0.05 + 0.03)
    # and above the body content area (CONTENT_TOP), giving a clean exhibit caption zone.
    label_top = TITLE_TOP + TITLE_HEIGHT + 0.12
    _add_textbox(
        slide,
        MARGIN_LEFT,
        label_top,
        CONTENT_WIDTH,
        0.25,
        node.exhibit_label,
        font_size=FONT_CAPTION,
        bold=True,
        color=muted,
        alignment=PP_ALIGN.LEFT,
        font_name=brand.body_font,
    )


def _render_footnotes(slide, node: SlideNode, brand: BrandConfig):
    """Render footnotes between body and source line."""
    if not node.footnotes:
        return
    muted = _muted_color_for_bg(node.background, brand)
    footnote_text = "  ".join(f"{i + 1}. {fn}" for i, fn in enumerate(node.footnotes))
    _add_textbox(
        slide,
        MARGIN_LEFT,
        SOURCE_TOP - 0.3,
        CONTENT_WIDTH,
        0.25,
        footnote_text,
        font_size=SOURCE_FONT,
        color=muted,
        alignment=PP_ALIGN.LEFT,
        font_name=brand.body_font,
    )


def _render_page_number(slide, page_num: int, node: SlideNode, brand: BrandConfig):
    """Render page number at bottom-right of slide."""
    muted = _muted_color_for_bg(node.background, brand)
    _add_textbox(
        slide,
        13.333 - MARGIN_RIGHT - PAGE_NUM_WIDTH,
        SOURCE_TOP,
        PAGE_NUM_WIDTH,
        SOURCE_HEIGHT,
        str(page_num),
        font_size=SOURCE_FONT,
        color=muted,
        alignment=PP_ALIGN.RIGHT,
        font_name=brand.body_font,
    )


# ── New Slide Type Renderers ─────────────────────────────────────


def _render_exec_summary(slide, node: SlideNode, brand: BrandConfig):
    """Render an executive summary slide with key messages."""
    text_color = _text_color_for_bg(node.background, brand)
    accent = resolve_color("accent", brand)

    # Heading
    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    _render_content_separator(slide, brand)

    # Executive summary bullets — numbered accent circles + evenly distributed
    # so bullets fill the full content height rather than clustering at the top.
    if node.bullets:
        n = len(node.bullets)
        # Spread bullets evenly across BODY_HEIGHT; cap slot at 1.1" so sparse
        # decks (2-3 bullets) don't leave bullets too far apart.
        slot = min(BODY_HEIGHT / n, 1.1)

        for i, bullet in enumerate(node.bullets):
            y = CONTENT_TOP + i * slot

            # Numbered accent circle
            circle = slide.shapes.add_shape(
                9,  # MSO_SHAPE.OVAL
                Inches(MARGIN_LEFT),
                Inches(y + 0.05),
                Inches(0.40),
                Inches(0.40),
            )
            circle.fill.solid()
            circle.fill.fore_color.rgb = accent
            circle.line.fill.background()
            ctf = circle.text_frame
            ctf.paragraphs[0].text = str(i + 1)
            ctf.paragraphs[0].font.size = Pt(11)
            ctf.paragraphs[0].font.bold = True
            ctf.paragraphs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            ctf.paragraphs[0].alignment = PP_ALIGN.CENTER

            # Bullet text — indented past the circle, sized to fit the slot
            txBox = slide.shapes.add_textbox(
                Inches(MARGIN_LEFT + 0.58),
                Inches(y),
                Inches(CONTENT_WIDTH - 0.58),
                Inches(max(slot - 0.08, 0.5)),
            )
            tf = txBox.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            _add_paragraph_runs(
                p, bullet.text, FONT_BODY + 1, color=text_color, font_name=brand.body_font
            )


def _render_next_steps(slide, node: SlideNode, brand: BrandConfig):
    """Render a next-steps slide as an action item table."""
    text_color = _text_color_for_bg(node.background, brand)

    # Heading
    if node.heading:
        _add_textbox(
            slide,
            TITLE_LEFT,
            TITLE_TOP,
            TITLE_WIDTH,
            TITLE_HEIGHT,
            node.heading,
            font_size=_heading_size(node.heading),
            bold=True,
            color=text_color,
            font_name=brand.header_font,
        )

    _render_content_separator(slide, brand)

    if not node.next_steps:
        # Fall back to bullets if no @action directives
        if node.bullets:
            _add_bullet_list(
                slide,
                MARGIN_LEFT,
                CONTENT_TOP,
                CONTENT_WIDTH,
                BODY_HEIGHT,
                node.bullets,
                font_size=FONT_BODY,
                color=text_color,
                font_name=brand.body_font,
            )
        return

    # Render as table: Action | Owner | Timeline
    headers = ["Action", "Owner", "Timeline"]
    row_count = len(node.next_steps) + 1  # +1 for header
    col_count = 3
    table_top = CONTENT_TOP + 0.2
    table_height = min(row_count * 0.6, 4.0)  # cap at 4.0" to clear footnote zone

    table_shape = slide.shapes.add_table(
        row_count,
        col_count,
        Inches(MARGIN_LEFT),
        Inches(table_top),
        Inches(CONTENT_WIDTH),
        Inches(table_height),
    )
    table = table_shape.table

    # Header row
    for j, hdr in enumerate(headers):
        cell = table.cell(0, j)
        cell.text = hdr
        p = cell.text_frame.paragraphs[0]
        p.font.bold = True
        p.font.size = Pt(FONT_BODY)
        p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        p.font.name = brand.header_font
        cell.fill.solid()
        cell.fill.fore_color.rgb = resolve_color("primary", brand)

    # Data rows
    for i, ns in enumerate(node.next_steps):
        values = [ns.action, ns.owner or "", ns.timeline or ""]
        for j, val in enumerate(values):
            cell = table.cell(i + 1, j)
            cell.text = val
            p = cell.text_frame.paragraphs[0]
            p.font.size = Pt(FONT_BODY - 1)
            p.font.name = brand.body_font
            if i % 2 == 1:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(0xF5, 0xF5, 0xF5)


# ── Speaker Notes ─────────────────────────────────────────────────


def _add_speaker_notes(slide, notes: str):
    """Add speaker notes to a slide."""
    notes_slide = slide.notes_slide
    notes_slide.notes_text_frame.text = notes


# ── Dispatch Table ────────────────────────────────────────────────

_RENDERERS = {
    SlideType.TITLE: _render_title,
    SlideType.SECTION_DIVIDER: _render_section_divider,
    SlideType.STAT_CALLOUT: _render_stat_callout,
    SlideType.BULLET_POINTS: _render_bullet_points,
    SlideType.TWO_COLUMN: _render_two_column,
    SlideType.COMPARISON: _render_comparison,
    SlideType.TIMELINE: _render_timeline,
    SlideType.IMAGE_TEXT: _render_image_text,
    SlideType.QUOTE: _render_quote,
    SlideType.CLOSING: _render_closing,
    SlideType.EXEC_SUMMARY: _render_exec_summary,
    SlideType.NEXT_STEPS: _render_next_steps,
    SlideType.FREEFORM: _render_freeform,
}


# ── Public API ────────────────────────────────────────────────────


def render(
    presentation: PresentationNode,
    output_dir: Path,
    template_path: Optional[str] = None,
) -> Path:
    """Render a PresentationNode to a .pptx file.

    Args:
        presentation: Parsed presentation from DSL.
        output_dir: Directory to write output file.
        template_path: Optional .pptx template (not yet implemented).

    Returns:
        Path to the generated .pptx file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    brand = presentation.meta.brand
    blank_layout = prs.slide_layouts[6]  # blank layout

    for page_num, node in enumerate(presentation.slides, start=1):
        slide = prs.slides.add_slide(blank_layout)

        # Background
        _apply_background(slide, node.background, brand)

        # Content via dispatch
        renderer_fn = _RENDERERS.get(node.slide_type, _render_freeform)
        renderer_fn(slide, node, brand)

        # Footer hairline rule + consulting metadata
        _render_footer_rule(slide, brand)
        _render_exhibit_label(slide, node, brand)
        _render_footnotes(slide, node, brand)
        _render_source_line(slide, node, brand)
        _render_page_number(slide, page_num, node, brand)

        # Confidentiality label centered at bottom of every slide
        if presentation.meta.confidentiality:
            _render_confidentiality_label(slide, presentation.meta, brand)

        # Speaker notes
        if node.speaker_notes:
            _add_speaker_notes(slide, node.speaker_notes)

    # Save
    safe_title = "".join(
        c if c.isalnum() or c in " -_" else "_" for c in presentation.meta.title
    ).strip()[:80]
    filename = f"{safe_title}.pptx"
    output_path = output_dir / filename
    prs.save(str(output_path))

    logger.info("Rendered %d slides to %s", len(presentation.slides), output_path)
    return output_path
