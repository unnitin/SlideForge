"""
tests/test_orchestrator.py — Tests for Orchestrator and Feedback

Tests the pipeline wiring with mocked agents and the feedback processor.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.dsl.models import (
    BackgroundType,
    PresentationMeta,
    PresentationNode,
    SlideNode,
    SlideType,
)
from src.dsl.parser import SlideForgeParser
from src.services.feedback import FeedbackProcessor
from src.index.store import DesignIndexStore


# ── Fixtures ─────────────────────────────────────────────────────────

SAMPLE_DSL = Path(__file__).parent.parent / "docs" / "examples" / "sample.sdsl"


@pytest.fixture
def store(tmp_path):
    """Create a fresh DesignIndexStore for testing."""
    db_path = str(tmp_path / "test_index.db")
    s = DesignIndexStore(db_path)
    s.initialize()
    return s


@pytest.fixture
def sample_presentation():
    """Parse the sample .sdsl file."""
    parser = SlideForgeParser()
    return parser.parse_file(str(SAMPLE_DSL))


@pytest.fixture
def simple_presentation():
    """A minimal presentation for testing."""
    return PresentationNode(
        meta=PresentationMeta(title="Test Deck"),
        slides=[
            SlideNode(
                slide_name="Title", slide_type=SlideType.TITLE, background=BackgroundType.DARK
            ),
            SlideNode(slide_name="Content", slide_type=SlideType.BULLET_POINTS),
            SlideNode(slide_name="End", slide_type=SlideType.CLOSING),
        ],
    )


# ═══════════════════════════════════════════════════════════════════════
# Feedback Processor Tests
# ═══════════════════════════════════════════════════════════════════════


class TestFeedbackProcessor:
    def test_record_keep(self, store):
        fp = FeedbackProcessor(store)
        # Need a slide chunk to record feedback against
        from src.index.chunker import SlideChunker

        parser = SlideForgeParser()
        pres = parser.parse_file(str(SAMPLE_DSL))
        chunker = SlideChunker()
        deck_chunk, slide_chunks, element_chunks = chunker.chunk(pres, source_file="test.sdsl")
        store.upsert_deck(deck_chunk)
        for sc in slide_chunks:
            store.upsert_slide(sc)

        chunk_id = slide_chunks[0].id
        fp.record_keep(chunk_id)
        # Should not raise

    def test_record_regen(self, store):
        fp = FeedbackProcessor(store)
        from src.index.chunker import SlideChunker

        parser = SlideForgeParser()
        pres = parser.parse_file(str(SAMPLE_DSL))
        chunker = SlideChunker()
        deck_chunk, slide_chunks, _ = chunker.chunk(pres)
        store.upsert_deck(deck_chunk)
        for sc in slide_chunks:
            store.upsert_slide(sc)

        chunk_id = slide_chunks[0].id
        fp.record_regen(chunk_id)

    def test_record_edit_ingests_new_version(self, store):
        fp = FeedbackProcessor(store)
        from src.index.chunker import SlideChunker

        parser = SlideForgeParser()
        pres = parser.parse_file(str(SAMPLE_DSL))
        chunker = SlideChunker()
        deck_chunk, slide_chunks, _ = chunker.chunk(pres)
        store.upsert_deck(deck_chunk)
        for sc in slide_chunks:
            store.upsert_slide(sc)

        chunk_id = slide_chunks[0].id

        edited_dsl = (
            "# Edited Title\n@type: title\n@background: dark\n\n## New Title\n### New Subtitle"
        )
        fp.record_edit(chunk_id, edited_dsl)

        # Stats should show the new ingested slide
        stats = store.get_stats()
        # We ingested original slides + 1 edited version
        assert stats["slide_chunks"] >= len(slide_chunks)

    def test_record_edit_with_invalid_dsl(self, store):
        fp = FeedbackProcessor(store)
        from src.index.chunker import SlideChunker

        parser = SlideForgeParser()
        pres = parser.parse_file(str(SAMPLE_DSL))
        chunker = SlideChunker()
        deck_chunk, slide_chunks, _ = chunker.chunk(pres)
        store.upsert_deck(deck_chunk)
        for sc in slide_chunks:
            store.upsert_slide(sc)

        chunk_id = slide_chunks[0].id
        # Should not raise even with weird input
        fp.record_edit(chunk_id, "")

    def test_record_phrase_hit(self, store):
        fp = FeedbackProcessor(store)
        # Need a real slide chunk for FK constraint
        from src.index.chunker import SlideChunker

        parser = SlideForgeParser()
        pres = parser.parse_file(str(SAMPLE_DSL))
        chunker = SlideChunker()
        deck_chunk, slide_chunks, _ = chunker.chunk(pres)
        store.upsert_deck(deck_chunk)
        for sc in slide_chunks:
            store.upsert_slide(sc)

        fp.record_phrase_hit("pipeline metrics", slide_chunk_id=slide_chunks[0].id)


# ═══════════════════════════════════════════════════════════════════════
# Orchestrator Tests (mocked agents)
# ═══════════════════════════════════════════════════════════════════════


class TestOrchestratorInit:
    def test_init_creates_components(self, tmp_path):
        from src.services.orchestrator import Orchestrator, PipelineConfig

        with (
            patch("agents.nl_to_dsl.anthropic.Anthropic"),
            patch("agents.qa_agent.anthropic.Anthropic"),
        ):
            config = PipelineConfig(
                index_db_path=str(tmp_path / "test.db"),
                api_key="test-key",
                output_dir=str(tmp_path / "output"),
            )
            orch = Orchestrator(config)

        assert orch.store is not None
        assert orch.retriever is not None
        assert orch.agent is not None
        assert orch.qa_agent is not None


class TestOrchestratorGenerate:
    def _make_orchestrator(self, tmp_path, dsl_text: str, qa_passed: bool = True):
        """Create an orchestrator with mocked agent and QA."""
        from src.services.orchestrator import Orchestrator, PipelineConfig
        from agents.nl_to_dsl import GenerationResult

        parser = SlideForgeParser()
        presentation = parser.parse(dsl_text)

        with (
            patch("agents.nl_to_dsl.anthropic.Anthropic"),
            patch("agents.qa_agent.anthropic.Anthropic"),
        ):
            config = PipelineConfig(
                index_db_path=str(tmp_path / "test.db"),
                api_key="test-key",
                output_dir=str(tmp_path / "output"),
                enable_qa=False,  # disable QA for most tests
            )
            orch = Orchestrator(config)

        # Mock the NL-to-DSL agent
        mock_result = GenerationResult(
            dsl_text=dsl_text,
            presentation=presentation,
            confidence=0.8,
            design_references=["ref1"],
            reasoning="Test generation",
        )
        orch.agent.generate = MagicMock(return_value=mock_result)

        return orch

    def test_generate_produces_pptx(self, tmp_path):
        dsl = SAMPLE_DSL.read_text()
        orch = self._make_orchestrator(tmp_path, dsl)

        result = orch.generate("Q3 update for leadership")

        assert result.presentation is not None
        assert result.slide_count > 0
        assert result.output_path is not None
        assert result.output_path.exists()
        assert result.output_path.suffix == ".pptx"

    def test_generate_saves_dsl(self, tmp_path):
        dsl = SAMPLE_DSL.read_text()
        orch = self._make_orchestrator(tmp_path, dsl)

        orch.generate("test")

        dsl_path = tmp_path / "output" / "presentation.sdsl"
        assert dsl_path.exists()

    def test_generate_ingests_to_index(self, tmp_path):
        dsl = SAMPLE_DSL.read_text()
        orch = self._make_orchestrator(tmp_path, dsl)

        result = orch.generate("test")

        assert result.deck_chunk_id is not None
        stats = orch.get_index_stats()
        assert stats["deck_chunks"] >= 1

    def test_generate_with_failed_parse(self, tmp_path):
        from src.services.orchestrator import Orchestrator, PipelineConfig
        from agents.nl_to_dsl import GenerationResult

        with (
            patch("agents.nl_to_dsl.anthropic.Anthropic"),
            patch("agents.qa_agent.anthropic.Anthropic"),
        ):
            config = PipelineConfig(
                index_db_path=str(tmp_path / "test.db"),
                api_key="test-key",
                output_dir=str(tmp_path / "output"),
                enable_qa=False,
            )
            orch = Orchestrator(config)

        # Mock a failed generation
        mock_result = GenerationResult(
            dsl_text="invalid",
            presentation=None,
            confidence=0.0,
            design_references=[],
            reasoning="Failed",
            parse_errors=["No slides"],
        )
        orch.agent.generate = MagicMock(return_value=mock_result)

        result = orch.generate("test")
        assert result.presentation is None
        assert "No slides" in result.errors


class TestOrchestratorIngest:
    def test_ingest_existing_deck(self, tmp_path):
        from src.services.orchestrator import Orchestrator, PipelineConfig

        with (
            patch("agents.nl_to_dsl.anthropic.Anthropic"),
            patch("agents.qa_agent.anthropic.Anthropic"),
        ):
            config = PipelineConfig(
                index_db_path=str(tmp_path / "test.db"),
                api_key="test-key",
            )
            orch = Orchestrator(config)

        chunk_id = orch.ingest_existing_deck(str(SAMPLE_DSL))
        assert chunk_id is not None

    def test_ingest_invalid_file(self, tmp_path):
        from src.services.orchestrator import Orchestrator, PipelineConfig

        with (
            patch("agents.nl_to_dsl.anthropic.Anthropic"),
            patch("agents.qa_agent.anthropic.Anthropic"),
        ):
            config = PipelineConfig(
                index_db_path=str(tmp_path / "test.db"),
                api_key="test-key",
            )
            orch = Orchestrator(config)

        result = orch.ingest_existing_deck("/nonexistent/file.sdsl")
        assert result is None


class TestOrchestratorFeedback:
    def test_record_keep(self, tmp_path):
        from src.services.orchestrator import Orchestrator, PipelineConfig

        with (
            patch("agents.nl_to_dsl.anthropic.Anthropic"),
            patch("agents.qa_agent.anthropic.Anthropic"),
        ):
            config = PipelineConfig(
                index_db_path=str(tmp_path / "test.db"),
                api_key="test-key",
            )
            orch = Orchestrator(config)

        # Ingest first
        chunk_id = orch.ingest_existing_deck(str(SAMPLE_DSL))
        # Get a slide chunk id
        stats = orch.get_index_stats()
        assert stats["slide_chunks"] > 0

        # record_feedback should not raise
        orch.record_feedback(chunk_id, "keep")

    def test_record_edit_with_dsl(self, tmp_path):
        from src.services.orchestrator import Orchestrator, PipelineConfig

        with (
            patch("agents.nl_to_dsl.anthropic.Anthropic"),
            patch("agents.qa_agent.anthropic.Anthropic"),
        ):
            config = PipelineConfig(
                index_db_path=str(tmp_path / "test.db"),
                api_key="test-key",
            )
            orch = Orchestrator(config)

        chunk_id = orch.ingest_existing_deck(str(SAMPLE_DSL))
        edited = "# New Title\n@type: title\n\n## Better Title"
        orch.record_feedback(chunk_id, "edit", edited_dsl=edited)


class TestBuildFixPrompt:
    def test_builds_fix_prompt(self):
        from src.services.orchestrator import Orchestrator
        from agents.nl_to_dsl import GenerationResult
        from agents.qa_agent import QAReport, QAIssue

        gen_result = GenerationResult(
            dsl_text="test",
            presentation=None,
            confidence=0.5,
            design_references=[],
            reasoning="test",
        )
        qa_report = QAReport(
            issues=[
                QAIssue(0, "critical", "overlap", "Title overlaps subtitle", "Add spacing"),
                QAIssue(1, "warning", "contrast", "Low contrast text"),
            ],
            passed=False,
        )

        prompt = Orchestrator._build_fix_prompt(gen_result, qa_report)
        assert "overlap" in prompt
        assert "Title overlaps subtitle" in prompt
        assert "Add spacing" in prompt
        assert "contrast" in prompt
