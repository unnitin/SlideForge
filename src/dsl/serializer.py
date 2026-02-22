"""
src/dsl/serializer.py — SlideForge Serializer

Converts PresentationNode → DSL text. Enables round-tripping:
  parse(serialize(parse(text))) ≡ parse(text)
"""

from __future__ import annotations

from .models import (
    BackgroundType,
    PresentationMeta,
    PresentationNode,
    SlideNode,
)


class SlideForgeSerializer:
    """Converts a PresentationNode back to .sdsl text."""

    def serialize(self, pres: PresentationNode) -> str:
        """Serialize a full presentation to DSL text."""
        parts = [self._frontmatter(pres.meta)]
        for slide in pres.slides:
            parts.append(self._slide(slide))
        return "\n\n---\n\n".join(parts) + "\n"

    def serialize_slide(self, slide: SlideNode) -> str:
        """Serialize a single slide (useful for index storage)."""
        return self._slide(slide)

    # ── Frontmatter ────────────────────────────────────────────────

    def _frontmatter(self, m: PresentationMeta) -> str:
        lines = ["---", "presentation:", f'  title: "{m.title}"']
        if m.author:
            lines.append(f'  author: "{m.author}"')
        if m.company:
            lines.append(f'  company: "{m.company}"')
        if m.date:
            lines.append(f'  date: "{m.date}"')
        if m.confidentiality:
            lines.append(f'  confidentiality: "{m.confidentiality}"')
        if m.template:
            lines.append(f'  template: "{m.template}"')
        lines.append(f'  output: "{m.output}"')
        lines.append("  brand:")
        b = m.brand
        lines.append(f'    primary: "{b.primary}"')
        lines.append(f'    secondary: "{b.secondary}"')
        lines.append(f'    accent: "{b.accent}"')
        lines.append(f'    header_font: "{b.header_font}"')
        lines.append(f'    body_font: "{b.body_font}"')
        if b.logo:
            lines.append(f'    logo: "{b.logo}"')
        lines.append("---")
        return "\n".join(lines)

    # ── Slide ──────────────────────────────────────────────────────

    def _slide(self, s: SlideNode) -> str:
        lines = [f"# {s.slide_name}"]
        lines.append(f"@type: {s.slide_type.value}")

        if s.background != BackgroundType.LIGHT:
            lines.append(f"@background: {s.background.value}")
        if s.layout:
            lines.append(f"@layout: {s.layout}")
        if s.image:
            lines.append(f"@image: {s.image}")

        lines.append("")

        # Headings
        if s.heading:
            lines.append(f"## {s.heading}")
        if s.subheading:
            lines.append(f"### {s.subheading}")

        # Stats
        for stat in s.stats:
            parts = [f"@stat: {stat.value} | {stat.label}"]
            if stat.description:
                parts.append(f" | {stat.description}")
            lines.append("".join(parts))

        # Timeline
        for step in s.timeline:
            parts = [f"@step: {step.time} | {step.title}"]
            if step.description:
                parts.append(f" | {step.description}")
            lines.append("".join(parts))

        # Columns
        for col in s.columns:
            lines.append("")
            lines.append("@col:")
            if col.title:
                lines.append(f"  ## {col.title}")
            for b in col.bullets:
                indent = "  " * (b.level + 1)
                lines.append(f"{indent}- {b.text}")

        # Comparison
        if s.compare:
            lines.append("")
            lines.append("@compare:")
            if s.compare.headers:
                lines.append(f"  header: {' | '.join(s.compare.headers)}")
            for row in s.compare.rows:
                lines.append(f"  row: {' | '.join(row)}")

        # Bullets
        for b in s.bullets:
            indent = "  " * b.level
            if b.icon:
                lines.append(f"{indent}- @icon: {b.icon} | {b.text}")
            else:
                lines.append(f"{indent}- {b.text}")

        # Next-steps / action items
        for ns in s.next_steps:
            parts = [f"@action: {ns.action}"]
            if ns.owner:
                parts.append(f" | {ns.owner}")
            if ns.timeline:
                parts.append(f" | {ns.timeline}")
            lines.append("".join(parts))

        # Exhibit label
        if s.exhibit_label:
            lines.append("")
            lines.append(f"@exhibit: {s.exhibit_label}")

        # Footnotes
        for fn in s.footnotes:
            lines.append(f"@footnote: {fn}")

        # Source line
        if s.source:
            lines.append(f"@source: {s.source}")

        # Speaker notes
        if s.speaker_notes:
            lines.append("")
            lines.append(f"@notes: {s.speaker_notes}")

        return "\n".join(lines)
