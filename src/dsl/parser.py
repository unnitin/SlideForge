"""
src/dsl/parser.py — SlideForge Parser

Parses .sdsl text into a PresentationNode. Intentionally lenient:
unknown directives are ignored, missing fields get defaults. This
makes it a reliable LLM generation target.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import (
    BackgroundType,
    BrandConfig,
    BulletItem,
    ColumnContent,
    CompareTable,
    NextStepItem,
    PresentationMeta,
    PresentationNode,
    SlideNode,
    SlideType,
    StatItem,
    TimelineStep,
)


class SlideForgeParser:
    """Parses SlideForge text → PresentationNode."""

    # ── Compiled patterns ──────────────────────────────────────────

    RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
    RE_SLIDE_SPLIT = re.compile(r"\n---\s*\n")
    RE_SLIDE_NAME = re.compile(r"^#\s+(.+)$", re.MULTILINE)
    RE_HEADING = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    RE_SUBHEADING = re.compile(r"^###\s+(.+)$", re.MULTILINE)
    RE_DIRECTIVE = re.compile(r"^@(\w+):\s*(.+)$", re.MULTILINE)
    RE_STAT = re.compile(r"^@stat:\s*(.+?)\s*\|\s*(.+?)(?:\s*\|\s*(.+))?\s*$", re.MULTILINE)
    RE_STEP = re.compile(r"^@step:\s*(.+?)\s*\|\s*(.+?)(?:\s*\|\s*(.+))?\s*$", re.MULTILINE)
    RE_BULLET = re.compile(r"^(\s*)-\s+(.+)$", re.MULTILINE)
    RE_ICON_BULLET = re.compile(r"^(\s*)-\s+@icon:\s*(\w+)\s*\|\s*(.+)$", re.MULTILINE)
    RE_COL_BLOCK = re.compile(r"@col:\s*\n((?:(?!@col:)[\s\S])*?)(?=@col:|\n---|\Z)", re.MULTILINE)
    RE_COMPARE_HEADER = re.compile(r"header:\s*(.+)$", re.MULTILINE)
    RE_COMPARE_ROW = re.compile(r"row:\s*(.+)$", re.MULTILINE)
    RE_NOTES = re.compile(r"@notes:\s*([\s\S]*?)(?=\n@|\n---|\Z)")
    RE_SOURCE = re.compile(r"^@source:\s*(.+)$", re.MULTILINE)
    RE_EXHIBIT = re.compile(r"^@exhibit:\s*(.+)$", re.MULTILINE)
    RE_FOOTNOTE = re.compile(r"^@footnote:\s*(.+)$", re.MULTILINE)
    RE_ACTION = re.compile(r"^@action:\s*(.+?)\s*\|\s*(.+?)(?:\s*\|\s*(.+))?\s*$", re.MULTILINE)

    def parse(self, dsl_text: str) -> PresentationNode:
        """Parse full DSL text into a PresentationNode."""
        meta = self._parse_frontmatter(dsl_text)
        body = self.RE_FRONTMATTER.sub("", dsl_text).strip()
        raw_slides = self.RE_SLIDE_SPLIT.split(body)

        slides: list[SlideNode] = []
        for raw in raw_slides:
            raw = raw.strip()
            if not raw:
                continue
            slide = self._parse_slide(raw)
            if slide:
                slides.append(slide)

        return PresentationNode(meta=meta, slides=slides)

    def parse_file(self, path: str) -> PresentationNode:
        """Parse a .sdsl file."""
        with open(path, "r", encoding="utf-8") as f:
            return self.parse(f.read())

    # ── Frontmatter ────────────────────────────────────────────────

    _FM_MAP = {
        "title": ("meta", "title"),
        "author": ("meta", "author"),
        "company": ("meta", "company"),
        "template": ("meta", "template"),
        "output": ("meta", "output"),
        "date": ("meta", "date"),
        "confidentiality": ("meta", "confidentiality"),
        "primary": ("brand", "primary"),
        "secondary": ("brand", "secondary"),
        "accent": ("brand", "accent"),
        "header_font": ("brand", "header_font"),
        "body_font": ("brand", "body_font"),
        "logo": ("brand", "logo"),
    }

    def _parse_frontmatter(self, text: str) -> PresentationMeta:
        match = self.RE_FRONTMATTER.search(text)
        if not match:
            return PresentationMeta()

        meta_kwargs: dict = {}
        brand_kwargs: dict = {}

        for line in match.group(1).split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if not val:
                continue

            if key in self._FM_MAP:
                target, attr = self._FM_MAP[key]
                if target == "meta":
                    meta_kwargs[attr] = val
                else:
                    brand_kwargs[attr] = val

        if brand_kwargs:
            meta_kwargs["brand"] = BrandConfig(**brand_kwargs)

        return PresentationMeta(**meta_kwargs)

    # ── Single Slide ───────────────────────────────────────────────

    def _parse_slide(self, text: str) -> Optional[SlideNode]:
        name_match = self.RE_SLIDE_NAME.search(text)
        if not name_match:
            return None

        kwargs: dict = {"slide_name": name_match.group(1).strip()}

        # Directives
        directives = {m.group(1): m.group(2).strip() for m in self.RE_DIRECTIVE.finditer(text)}

        if "type" in directives:
            try:
                kwargs["slide_type"] = SlideType(directives["type"])
            except ValueError:
                kwargs["slide_type"] = SlideType.FREEFORM

        if "background" in directives:
            try:
                kwargs["background"] = BackgroundType(directives["background"])
            except ValueError:
                pass

        if "layout" in directives:
            kwargs["layout"] = directives["layout"]
        if "image" in directives:
            kwargs["image"] = directives["image"]

        # Headings
        h = self.RE_HEADING.search(text)
        if h:
            kwargs["heading"] = h.group(1).strip()
        sh = self.RE_SUBHEADING.search(text)
        if sh:
            kwargs["subheading"] = sh.group(1).strip()

        # Stats
        stats = [
            StatItem(
                value=m.group(1).strip(),
                label=m.group(2).strip(),
                description=m.group(3).strip() if m.group(3) else None,
            )
            for m in self.RE_STAT.finditer(text)
        ]
        if stats:
            kwargs["stats"] = stats

        # Timeline
        timeline = [
            TimelineStep(
                time=m.group(1).strip(),
                title=m.group(2).strip(),
                description=m.group(3).strip() if m.group(3) else None,
            )
            for m in self.RE_STEP.finditer(text)
        ]
        if timeline:
            kwargs["timeline"] = timeline

        # Columns
        columns = [self._parse_column(m.group(1)) for m in self.RE_COL_BLOCK.finditer(text)]
        if columns:
            kwargs["columns"] = columns

        # Comparison
        if "@compare:" in text:
            kwargs["compare"] = self._parse_compare(text)

        # Bullets (only if not already captured in columns)
        if not columns:
            bullets = self._parse_bullets(text)
            if bullets:
                kwargs["bullets"] = bullets

        # Source line
        source_match = self.RE_SOURCE.search(text)
        if source_match:
            kwargs["source"] = source_match.group(1).strip()

        # Exhibit label
        exhibit_match = self.RE_EXHIBIT.search(text)
        if exhibit_match:
            kwargs["exhibit_label"] = exhibit_match.group(1).strip()

        # Footnotes
        footnotes = [m.group(1).strip() for m in self.RE_FOOTNOTE.finditer(text)]
        if footnotes:
            kwargs["footnotes"] = footnotes

        # Next-steps / action items
        actions = [
            NextStepItem(
                action=m.group(1).strip(),
                owner=m.group(2).strip() if m.group(2) else None,
                timeline=m.group(3).strip() if m.group(3) else None,
            )
            for m in self.RE_ACTION.finditer(text)
        ]
        if actions:
            kwargs["next_steps"] = actions

        # Speaker notes
        notes_match = self.RE_NOTES.search(text)
        if notes_match:
            kwargs["speaker_notes"] = notes_match.group(1).strip()

        return SlideNode(**kwargs)

    def _parse_bullets(self, text: str) -> list[BulletItem]:
        """Parse bullets, preferring icon bullets if present."""
        icon_bullets = [
            BulletItem(
                text=m.group(3).strip(),
                level=len(m.group(1)) // 2,
                icon=m.group(2).strip(),
            )
            for m in self.RE_ICON_BULLET.finditer(text)
        ]
        if icon_bullets:
            return icon_bullets

        return [
            BulletItem(text=m.group(2).strip(), level=len(m.group(1)) // 2)
            for m in self.RE_BULLET.finditer(text)
        ]

    def _parse_column(self, text: str) -> ColumnContent:
        col_kwargs: dict = {}
        # Column headings may be indented, so use a more lenient pattern
        h = re.search(r"^\s*##\s+(.+)$", text, re.MULTILINE)
        if h:
            col_kwargs["title"] = h.group(1).strip()
        bullets = [
            BulletItem(text=m.group(2).strip(), level=len(m.group(1)) // 2)
            for m in self.RE_BULLET.finditer(text)
        ]
        if bullets:
            col_kwargs["bullets"] = bullets
        return ColumnContent(**col_kwargs)

    def _parse_compare(self, text: str) -> CompareTable:
        kwargs: dict = {}
        h = self.RE_COMPARE_HEADER.search(text)
        if h:
            kwargs["headers"] = [c.strip() for c in h.group(1).split("|")]
        rows = [
            [c.strip() for c in m.group(1).split("|")] for m in self.RE_COMPARE_ROW.finditer(text)
        ]
        if rows:
            kwargs["rows"] = rows
        return CompareTable(**kwargs)
