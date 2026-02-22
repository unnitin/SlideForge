"""
tests/test_parser.py — Tests for SlideForge parser

Validates parsing of the sample.sdsl fixture and edge cases.
Run with: pytest tests/test_parser.py -v
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dsl.models import BackgroundType, SlideType
from src.dsl.parser import SlideForgeParser


SAMPLE_PATH = Path(__file__).parent.parent / "docs" / "examples" / "sample.sdsl"


def _load_sample() -> str:
    return SAMPLE_PATH.read_text(encoding="utf-8")


class TestFrontmatter:
    def test_parses_title(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.meta.title == "Q3 2025 Data Platform Update"

    def test_parses_author(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.meta.author == "Nitin"

    def test_parses_company(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.meta.company == "Create Music Group"

    def test_parses_brand_colors(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.meta.brand.primary == "1E2761"
        assert pres.meta.brand.accent == "F96167"

    def test_parses_template_path(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.meta.template == "./templates/cmg_brand.pptx"

    def test_missing_frontmatter_returns_defaults(self):
        parser = SlideForgeParser()
        pres = parser.parse("# Just a slide\n@type: title\n## Hello")
        assert pres.meta.title == "Untitled Presentation"
        assert pres.meta.brand.primary == "1E2761"


class TestSlideCount:
    def test_sample_has_expected_slides(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        # Title, Section, Stats, TwoCol, Section, Timeline, Comparison,
        # Section, Bullets, Stats, Closing = 11
        assert len(pres.slides) == 11


class TestSlideTypes:
    def test_title_slide(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[0].slide_type == SlideType.TITLE

    def test_section_divider(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[1].slide_type == SlideType.SECTION_DIVIDER

    def test_stat_callout(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[2].slide_type == SlideType.STAT_CALLOUT

    def test_two_column(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[3].slide_type == SlideType.TWO_COLUMN

    def test_timeline(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[5].slide_type == SlideType.TIMELINE

    def test_comparison(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[6].slide_type == SlideType.COMPARISON

    def test_bullet_points(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[8].slide_type == SlideType.BULLET_POINTS

    def test_closing(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[-1].slide_type == SlideType.CLOSING


class TestBackgrounds:
    def test_dark_background(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[0].background == BackgroundType.DARK

    def test_gradient_background(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[1].background == BackgroundType.GRADIENT

    def test_default_light(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[2].background == BackgroundType.LIGHT


class TestStats:
    def test_stat_count(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        stat_slide = pres.slides[2]  # Medallion Architecture Progress
        assert len(stat_slide.stats) == 3

    def test_stat_values(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        stats = pres.slides[2].stats
        assert stats[0].value == "94%"
        assert stats[0].label == "Pipeline Uptime"
        assert stats[0].description == "Up from 87% in Q2"

    def test_stat_without_description(self):
        parser = SlideForgeParser()
        pres = parser.parse("# Test\n@type: stat_callout\n@stat: 42 | The Answer\n")
        assert pres.slides[0].stats[0].description is None


class TestColumns:
    def test_two_columns_parsed(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        col_slide = pres.slides[3]  # Contract Hardening Strategy
        assert len(col_slide.columns) == 2

    def test_column_titles(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        cols = pres.slides[3].columns
        assert cols[0].title == "Streaming (Pub/Sub)"
        assert cols[1].title == "Table-Based (BigQuery)"

    def test_column_bullets(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        cols = pres.slides[3].columns
        assert len(cols[0].bullets) >= 3
        assert len(cols[1].bullets) >= 3


class TestTimeline:
    def test_timeline_steps(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        timeline_slide = pres.slides[5]
        assert len(timeline_slide.timeline) == 5

    def test_timeline_values(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        steps = pres.slides[5].timeline
        assert steps[0].time == "Jan 2025"
        assert steps[0].title == "Joined CMG"
        assert steps[0].description is not None


class TestComparison:
    def test_compare_headers(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        compare_slide = pres.slides[6]
        assert compare_slide.compare is not None
        assert len(compare_slide.compare.headers) == 3
        assert compare_slide.compare.headers[0] == "Risk"

    def test_compare_rows(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        compare_slide = pres.slides[6]
        assert len(compare_slide.compare.rows) == 4


class TestBullets:
    def test_icon_bullets(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        bullet_slide = pres.slides[8]  # Q4 Priorities
        assert len(bullet_slide.bullets) == 5
        assert bullet_slide.bullets[0].icon == "rocket"
        assert bullet_slide.layout == "icon_rows"


class TestSpeakerNotes:
    def test_notes_present(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[0].speaker_notes is not None
        assert "Welcome" in pres.slides[0].speaker_notes


class TestHeadings:
    def test_heading(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert pres.slides[0].heading == "Q3 2025 Data Platform Update"

    def test_subheading(self):
        parser = SlideForgeParser()
        pres = parser.parse(_load_sample())
        assert "Data & AI Organization" in pres.slides[0].subheading


class TestEdgeCases:
    def test_empty_input(self):
        parser = SlideForgeParser()
        pres = parser.parse("")
        assert len(pres.slides) == 0

    def test_frontmatter_only(self):
        parser = SlideForgeParser()
        pres = parser.parse('---\npresentation:\n  title: "Test"\n---\n')
        assert pres.meta.title == "Test"
        assert len(pres.slides) == 0

    def test_unknown_slide_type_becomes_freeform(self):
        parser = SlideForgeParser()
        pres = parser.parse("# Test\n@type: unknown_type\n## Hello")
        assert pres.slides[0].slide_type == SlideType.FREEFORM

    def test_missing_type_defaults_to_freeform(self):
        parser = SlideForgeParser()
        pres = parser.parse("# Test\n## Hello World")
        assert pres.slides[0].slide_type == SlideType.FREEFORM
