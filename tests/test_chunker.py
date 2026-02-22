"""
tests/test_chunker.py — Tests for multi-granularity chunking
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dsl.parser import SlideForgeParser
from src.index.chunker import SlideChunker

SAMPLE_PATH = Path(__file__).parent.parent / "docs" / "examples" / "sample.sdsl"


def _chunk_sample():
    parser = SlideForgeParser()
    pres = parser.parse(SAMPLE_PATH.read_text(encoding="utf-8"))
    chunker = SlideChunker()
    return chunker.chunk(pres, source_file=str(SAMPLE_PATH))


class TestDeckChunk:
    def test_deck_title(self):
        deck, _, _ = _chunk_sample()
        assert deck.title == "Q3 2025 Data Platform Update"

    def test_slide_count(self):
        deck, _, _ = _chunk_sample()
        assert deck.slide_count == 11

    def test_slide_type_sequence(self):
        deck, _, _ = _chunk_sample()
        assert deck.slide_type_sequence[0] == "title"
        assert deck.slide_type_sequence[-1] == "closing"

    def test_brand_colors(self):
        deck, _, _ = _chunk_sample()
        assert "1E2761" in deck.brand_colors

    def test_slide_chunk_ids_populated(self):
        deck, slides, _ = _chunk_sample()
        assert len(deck.slide_chunk_ids) == len(slides)

    def test_embedding_text_nonempty(self):
        deck, _, _ = _chunk_sample()
        assert len(deck.embedding_text()) > 50


class TestSlideChunks:
    def test_correct_count(self):
        _, slides, _ = _chunk_sample()
        assert len(slides) == 11

    def test_structural_fingerprint_stats(self):
        _, slides, _ = _chunk_sample()
        stat_slide = slides[2]  # Medallion Architecture Progress
        assert stat_slide.has_stats is True
        assert stat_slide.stat_count == 3

    def test_structural_fingerprint_columns(self):
        _, slides, _ = _chunk_sample()
        col_slide = slides[3]  # Contract Hardening
        assert col_slide.has_columns is True
        assert col_slide.column_count == 2

    def test_structural_fingerprint_timeline(self):
        _, slides, _ = _chunk_sample()
        timeline_slide = slides[5]  # Team Build-Out
        assert timeline_slide.has_timeline is True
        assert timeline_slide.step_count == 5

    def test_structural_fingerprint_comparison(self):
        _, slides, _ = _chunk_sample()
        compare_slide = slides[6]  # Key Risks
        assert compare_slide.has_comparison is True

    def test_structural_fingerprint_icons(self):
        _, slides, _ = _chunk_sample()
        bullet_slide = slides[8]  # Q4 Priorities
        assert bullet_slide.has_icons is True
        assert bullet_slide.has_bullets is True

    def test_neighborhood_context(self):
        _, slides, _ = _chunk_sample()
        # Slide 2 (stat_callout) should have section_divider before it
        assert slides[2].prev_slide_type == "section_divider"

    def test_deck_position_opening(self):
        _, slides, _ = _chunk_sample()
        assert slides[0].deck_position == "opening"

    def test_deck_position_closing(self):
        _, slides, _ = _chunk_sample()
        assert slides[-1].deck_position == "closing"

    def test_section_tracking(self):
        _, slides, _ = _chunk_sample()
        # Slides after "Section: Platform Health" should have that section
        assert slides[2].section_name is not None

    def test_dsl_text_populated(self):
        _, slides, _ = _chunk_sample()
        for slide in slides:
            assert len(slide.dsl_text) > 10

    def test_embedding_text_includes_type(self):
        _, slides, _ = _chunk_sample()
        text = slides[2].embedding_text()
        assert "stat_callout" in text

    def test_quality_score_default(self):
        _, slides, _ = _chunk_sample()
        assert slides[0].quality_score == 0.5  # no interactions yet


class TestElementChunks:
    def test_elements_created(self):
        _, _, elements = _chunk_sample()
        assert len(elements) > 0

    def test_stat_elements(self):
        _, _, elements = _chunk_sample()
        stat_elements = [e for e in elements if e.element_type == "stat"]
        # 3 stats in slide 2 + 3 stats in slide 9
        assert len(stat_elements) >= 6

    def test_stat_raw_content(self):
        _, _, elements = _chunk_sample()
        stat = next(e for e in elements if e.element_type == "stat")
        assert "value" in stat.raw_content
        assert "label" in stat.raw_content

    def test_column_elements(self):
        _, _, elements = _chunk_sample()
        col_elements = [e for e in elements if e.element_type == "column"]
        assert len(col_elements) >= 2

    def test_timeline_step_elements(self):
        _, _, elements = _chunk_sample()
        steps = [e for e in elements if e.element_type == "timeline_step"]
        assert len(steps) == 5

    def test_comparison_row_elements(self):
        _, _, elements = _chunk_sample()
        rows = [e for e in elements if e.element_type == "comparison_row"]
        assert len(rows) == 4

    def test_icon_bullet_group(self):
        _, _, elements = _chunk_sample()
        icon_groups = [e for e in elements if e.element_type == "icon_bullet_group"]
        assert len(icon_groups) >= 1
        assert icon_groups[0].raw_content["has_icons"] is True

    def test_heading_elements(self):
        _, _, elements = _chunk_sample()
        headings = [e for e in elements if e.element_type == "heading"]
        assert len(headings) > 0

    def test_sibling_count(self):
        _, _, elements = _chunk_sample()
        stat = next(e for e in elements if e.element_type == "stat")
        assert stat.sibling_count >= 3

    def test_parent_references(self):
        deck, slides, elements = _chunk_sample()
        for elem in elements:
            assert elem.deck_chunk_id == deck.id
            assert elem.slide_chunk_id in [s.id for s in slides]

    def test_embedding_text_includes_type(self):
        _, _, elements = _chunk_sample()
        stat = next(e for e in elements if e.element_type == "stat")
        text = stat.embedding_text()
        assert "stat" in text
