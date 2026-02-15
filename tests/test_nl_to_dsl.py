"""
tests/test_nl_to_dsl.py — Tests for the NL-to-DSL Translation Agent

Uses mocked Anthropic API calls to test agent logic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


from agents.nl_to_dsl import (
    GenerationContext,
    NLToDSLAgent,
    _strip_fences,
)
from src.dsl.models import SlideType


# ── Helpers ─────────────────────────────────────────────────────────

SAMPLE_DSL = Path(__file__).parent.parent / "docs" / "examples" / "sample.sdsl"


def _make_agent(response_text: str) -> NLToDSLAgent:
    """Create agent with mocked Anthropic client."""
    with patch("agents.nl_to_dsl.anthropic.Anthropic"):
        agent = NLToDSLAgent.__new__(NLToDSLAgent)
        agent.client = MagicMock()
        agent.model = "test-model"
        agent.MAX_RETRIES = 2

    from src.dsl.parser import SlideDSLParser

    agent.parser = SlideDSLParser()
    agent._system_prompt = "You are a test."

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=response_text)]
    agent.client.messages.create.return_value = mock_response

    return agent


# ── strip_fences helper ─────────────────────────────────────────────


class TestStripFences:
    def test_no_fences(self):
        assert _strip_fences("hello world") == "hello world"

    def test_basic_fences(self):
        result = _strip_fences("```\nsome code\n```")
        assert result == "some code"

    def test_language_fences(self):
        result = _strip_fences("```sdsl\n# Title\n@type: title\n```")
        assert result == "# Title\n@type: title"

    def test_whitespace_around(self):
        result = _strip_fences("  ```\ncontent\n```  ")
        assert result == "content"

    def test_no_closing_fence(self):
        result = _strip_fences("```\nopen ended")
        assert result == "open ended"


# ── GenerationContext ────────────────────────────────────────────────


class TestGenerationContext:
    def test_defaults(self):
        ctx = GenerationContext(user_input="test")
        assert ctx.similar_slides == []
        assert ctx.similar_decks == []
        assert ctx.output_format == "pptx"
        assert ctx.audience == "general"

    def test_with_all_fields(self):
        ctx = GenerationContext(
            user_input="Q3 update",
            target_slide_count=8,
            audience="leadership",
            output_format="ee4p",
        )
        assert ctx.target_slide_count == 8
        assert ctx.audience == "leadership"


# ── Confidence Estimation ────────────────────────────────────────────


class TestConfidenceEstimation:
    def test_title_and_closing_boost(self):
        agent = _make_agent("")

        dsl_text = SAMPLE_DSL.read_text()
        presentation = agent.parser.parse(dsl_text)

        ctx = GenerationContext(user_input="test")
        score = agent._estimate_confidence(presentation, ctx)

        # sample.sdsl has title and closing + varied types
        assert score >= 0.7

    def test_low_confidence_empty(self):
        agent = _make_agent("")

        from src.dsl.models import PresentationNode, PresentationMeta, SlideNode

        pres = PresentationNode(
            meta=PresentationMeta(title="Empty"),
            slides=[
                SlideNode(slide_name="Only", slide_type=SlideType.FREEFORM),
            ],
        )
        ctx = GenerationContext(user_input="test")
        score = agent._estimate_confidence(pres, ctx)
        assert score == 0.5  # base score, no bonuses

    def test_target_slide_count_match(self):
        agent = _make_agent("")
        from src.dsl.models import PresentationNode, PresentationMeta, SlideNode

        pres = PresentationNode(
            meta=PresentationMeta(title="Test"),
            slides=[
                SlideNode(slide_name="Title", slide_type=SlideType.TITLE),
                SlideNode(slide_name="Content", slide_type=SlideType.BULLET_POINTS),
                SlideNode(slide_name="Stats", slide_type=SlideType.STAT_CALLOUT),
                SlideNode(slide_name="Close", slide_type=SlideType.CLOSING),
            ],
        )
        ctx = GenerationContext(user_input="test", target_slide_count=4)
        score = agent._estimate_confidence(pres, ctx)
        # title + closing + 3 unique types + exact count match
        assert score >= 0.89


# ── Prompt Building ──────────────────────────────────────────────────


class TestPromptBuilding:
    def test_basic_prompt(self):
        agent = _make_agent("")
        ctx = GenerationContext(user_input="Make a Q3 update")
        prompt = agent._build_prompt(ctx)
        assert "Q3 update" in prompt

    def test_prompt_with_source_docs(self):
        agent = _make_agent("")
        ctx = GenerationContext(
            user_input="Summarize this",
            source_documents=["Doc content here"],
        )
        prompt = agent._build_prompt(ctx)
        assert "Source Material" in prompt
        assert "Doc content here" in prompt

    def test_prompt_with_existing_dsl(self):
        agent = _make_agent("")
        ctx = GenerationContext(
            user_input="Edit this",
            existing_dsl="# Title\n@type: title",
        )
        prompt = agent._build_prompt(ctx)
        assert "Existing Deck" in prompt

    def test_retry_prompt(self):
        agent = _make_agent("")
        prompt = agent._retry_prompt(["No slides found", "Parse error"])
        assert "No slides found" in prompt
        assert "Parse error" in prompt


# ── Generation (mocked API) ─────────────────────────────────────────


class TestGenerate:
    def test_successful_generation(self):
        dsl = SAMPLE_DSL.read_text()
        agent = _make_agent(dsl)

        ctx = GenerationContext(user_input="Q3 update")
        result = agent.generate(ctx)

        assert result.presentation is not None
        assert result.confidence > 0
        assert len(result.parse_errors) == 0
        assert "Generated" in result.reasoning

    def test_generation_with_fences(self):
        dsl = SAMPLE_DSL.read_text()
        fenced = f"```sdsl\n{dsl}\n```"
        agent = _make_agent(fenced)

        ctx = GenerationContext(user_input="test")
        result = agent.generate(ctx)

        assert result.presentation is not None

    def test_generation_failure_returns_partial(self):
        agent = _make_agent("this is not valid DSL at all")
        ctx = GenerationContext(user_input="test")
        result = agent.generate(ctx)

        # Parser is lenient, but there should be no slides
        # (depends on parser behavior with plain text)
        assert result.dsl_text is not None

    def test_api_called_once_on_success(self):
        dsl = SAMPLE_DSL.read_text()
        agent = _make_agent(dsl)

        ctx = GenerationContext(user_input="test")
        agent.generate(ctx)

        assert agent.client.messages.create.call_count == 1
