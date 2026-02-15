"""
tests/test_serializer.py — Tests for DSL Serializer round-tripping

Covers serialization of all content elements and round-trip parse→serialize→parse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.dsl.models import (
    BackgroundType,
    BrandConfig,
    BulletItem,
    ColumnContent,
    CompareTable,
    PresentationMeta,
    PresentationNode,
    SlideNode,
    SlideType,
    StatItem,
    TimelineStep,
)
from src.dsl.parser import SlideDSLParser
from src.dsl.serializer import SlideDSLSerializer

SAMPLE_DSL = Path(__file__).parent.parent / "docs" / "examples" / "sample.sdsl"


@pytest.fixture
def serializer():
    return SlideDSLSerializer()


@pytest.fixture
def parser():
    return SlideDSLParser()


# ── Round-trip tests ─────────────────────────────────────────────────


class TestRoundTrip:
    def test_sample_file_round_trips(self, parser, serializer):
        """parse(serialize(parse(text))) produces same result as parse(text)."""
        original_text = SAMPLE_DSL.read_text()
        pres1 = parser.parse(original_text)

        serialized = serializer.serialize(pres1)
        pres2 = parser.parse(serialized)

        assert pres1.meta.title == pres2.meta.title
        assert pres1.meta.author == pres2.meta.author
        assert pres1.meta.company == pres2.meta.company
        assert len(pres1.slides) == len(pres2.slides)

        for s1, s2 in zip(pres1.slides, pres2.slides):
            assert s1.slide_name == s2.slide_name
            assert s1.slide_type == s2.slide_type
            assert s1.background == s2.background


# ── Frontmatter serialization ────────────────────────────────────────


class TestFrontmatter:
    def test_minimal_frontmatter(self, serializer):
        meta = PresentationMeta(title="Test")
        pres = PresentationNode(meta=meta, slides=[])
        text = serializer.serialize(pres)
        assert 'title: "Test"' in text
        assert "---" in text

    def test_full_frontmatter(self, serializer):
        meta = PresentationMeta(
            title="Q3 Update",
            author="Alice",
            company="Acme",
            template="./brand.pptx",
            output="ee4p",
            brand=BrandConfig(
                primary="FF0000",
                secondary="00FF00",
                accent="0000FF",
                header_font="Georgia",
                body_font="Verdana",
                logo="./logo.png",
            ),
        )
        pres = PresentationNode(meta=meta, slides=[])
        text = serializer.serialize(pres)
        assert 'author: "Alice"' in text
        assert 'company: "Acme"' in text
        assert 'template: "./brand.pptx"' in text
        assert 'output: "ee4p"' in text
        assert 'primary: "FF0000"' in text
        assert 'logo: "./logo.png"' in text


# ── Slide serialization ─────────────────────────────────────────────


class TestSlideSerializer:
    def test_title_slide(self, serializer):
        slide = SlideNode(
            slide_name="Opening",
            slide_type=SlideType.TITLE,
            background=BackgroundType.DARK,
            heading="Welcome",
            subheading="Conference 2025",
        )
        text = serializer.serialize_slide(slide)
        assert "# Opening" in text
        assert "@type: title" in text
        assert "@background: dark" in text
        assert "## Welcome" in text
        assert "### Conference 2025" in text

    def test_light_background_omitted(self, serializer):
        slide = SlideNode(
            slide_name="Plain",
            slide_type=SlideType.BULLET_POINTS,
            background=BackgroundType.LIGHT,
        )
        text = serializer.serialize_slide(slide)
        assert "@background" not in text

    def test_layout_serialized(self, serializer):
        slide = SlideNode(
            slide_name="Icons",
            slide_type=SlideType.BULLET_POINTS,
            layout="icon_rows",
        )
        text = serializer.serialize_slide(slide)
        assert "@layout: icon_rows" in text

    def test_image_directive(self, serializer):
        slide = SlideNode(
            slide_name="Photo",
            slide_type=SlideType.IMAGE_TEXT,
            image="./photos/team.jpg",
        )
        text = serializer.serialize_slide(slide)
        assert "@image: ./photos/team.jpg" in text

    def test_stats_serialized(self, serializer):
        slide = SlideNode(
            slide_name="KPIs",
            slide_type=SlideType.STAT_CALLOUT,
            stats=[
                StatItem(value="94%", label="Uptime", description="Up from 87%"),
                StatItem(value="3.2B", label="Events"),
            ],
        )
        text = serializer.serialize_slide(slide)
        assert "@stat: 94% | Uptime | Up from 87%" in text
        assert "@stat: 3.2B | Events" in text
        # No trailing " | " for stat without description
        lines = text.split("\n")
        events_line = [line for line in lines if "Events" in line][0]
        assert events_line.strip() == "@stat: 3.2B | Events"

    def test_timeline_serialized(self, serializer):
        slide = SlideNode(
            slide_name="History",
            slide_type=SlideType.TIMELINE,
            timeline=[
                TimelineStep(time="Q1", title="Started", description="Kicked off project"),
                TimelineStep(time="Q2", title="Shipped"),
            ],
        )
        text = serializer.serialize_slide(slide)
        assert "@step: Q1 | Started | Kicked off project" in text
        assert "@step: Q2 | Shipped" in text

    def test_columns_serialized(self, serializer):
        slide = SlideNode(
            slide_name="Comparison",
            slide_type=SlideType.TWO_COLUMN,
            columns=[
                ColumnContent(
                    title="Left Side",
                    bullets=[BulletItem(text="Point A"), BulletItem(text="Sub", level=1)],
                ),
                ColumnContent(
                    title="Right Side",
                    bullets=[BulletItem(text="Point B")],
                ),
            ],
        )
        text = serializer.serialize_slide(slide)
        assert "@col:" in text
        assert "## Left Side" in text
        assert "- Point A" in text
        assert "- Sub" in text
        assert "## Right Side" in text

    def test_comparison_serialized(self, serializer):
        slide = SlideNode(
            slide_name="Compare",
            slide_type=SlideType.COMPARISON,
            compare=CompareTable(
                headers=["Feature", "Before", "After"],
                rows=[
                    ["Speed", "Slow", "Fast"],
                    ["Cost", "$100", "$50"],
                ],
            ),
        )
        text = serializer.serialize_slide(slide)
        assert "@compare:" in text
        assert "header: Feature | Before | After" in text
        assert "row: Speed | Slow | Fast" in text
        assert "row: Cost | $100 | $50" in text

    def test_bullets_with_icons(self, serializer):
        slide = SlideNode(
            slide_name="Features",
            slide_type=SlideType.BULLET_POINTS,
            bullets=[
                BulletItem(text="Fast deployment", icon="rocket"),
                BulletItem(text="Plain bullet"),
                BulletItem(text="Sub item", level=1),
            ],
        )
        text = serializer.serialize_slide(slide)
        assert "- @icon: rocket | Fast deployment" in text
        assert "- Plain bullet" in text

    def test_speaker_notes(self, serializer):
        slide = SlideNode(
            slide_name="With Notes",
            slide_type=SlideType.FREEFORM,
            speaker_notes="Remember to pause here.",
        )
        text = serializer.serialize_slide(slide)
        assert "@notes: Remember to pause here." in text
