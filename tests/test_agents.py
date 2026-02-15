"""
tests/test_agents.py — Tests for QA Agent and Index Curator

Uses mocked Anthropic API calls to test agent logic without real API access.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


from src.dsl.models import SlideNode, SlideType, BackgroundType
from src.dsl.parser import SlideDSLParser


# ═══════════════════════════════════════════════════════════════════════
# QA Agent Tests
# ═══════════════════════════════════════════════════════════════════════


class TestQAIssue:
    def test_qa_issue_defaults(self):
        from agents.qa_agent import QAIssue

        issue = QAIssue(
            slide_index=0,
            severity="critical",
            category="overlap",
            description="Text overlaps image",
        )
        assert issue.suggested_fix is None
        assert issue.severity == "critical"


class TestQAReport:
    def test_empty_report_fails(self):
        from agents.qa_agent import QAReport

        report = QAReport()
        assert report.passed is False
        assert report.critical_count == 0

    def test_report_with_no_critical_passes(self):
        from agents.qa_agent import QAIssue, QAReport

        report = QAReport(
            issues=[
                QAIssue(0, "warning", "alignment", "Slight misalignment"),
                QAIssue(1, "minor", "spacing", "Extra whitespace"),
            ],
            passed=True,
            summary="PASS",
        )
        assert report.passed is True
        assert report.critical_count == 0
        assert report.warning_count == 1

    def test_report_critical_count(self):
        from agents.qa_agent import QAIssue, QAReport

        report = QAReport(
            issues=[
                QAIssue(0, "critical", "overlap", "Text overlaps image"),
                QAIssue(1, "critical", "overflow", "Text cut off"),
                QAIssue(1, "warning", "contrast", "Low contrast"),
            ],
            passed=False,
            summary="FAIL",
        )
        assert report.critical_count == 2
        assert report.warning_count == 1


class TestQAResponseParsing:
    """Test the QA agent's response parser without API calls."""

    def _get_agent_with_mock(self):
        from agents.qa_agent import QAAgent

        with patch("agents.qa_agent.anthropic.Anthropic"):
            agent = QAAgent.__new__(QAAgent)
            agent.client = MagicMock()
            agent.model = "test"
            agent.serializer = MagicMock()
            agent._system_prompt = "test"
        return agent

    def test_parse_clean_report(self):
        agent = self._get_agent_with_mock()
        text = (
            "SLIDE 1: Title Slide — No issues found\n"
            "SLIDE 2: Metrics — No issues found\n\n"
            "PASS: All slides acceptable (no critical issues)"
        )
        report = agent._parse_response(text)
        assert report.passed is True
        assert len(report.issues) == 0

    def test_parse_issues_with_fixes(self):
        agent = self._get_agent_with_mock()
        text = (
            "SLIDE 1: Title Slide\n"
            "- [CRITICAL] overlap: Title text overlaps subtitle\n"
            "  Suggested fix: Increase spacing between title and subtitle\n"
            "- [WARNING] contrast: Low contrast on dark background\n\n"
            "SLIDE 2: Metrics\n"
            "- [MINOR] spacing: Uneven gaps between stat cards\n"
            "  Suggested fix: Set equal horizontal gaps\n\n"
            "FAIL: 1 critical issues need fixing before delivery"
        )
        report = agent._parse_response(text)
        assert report.passed is False
        assert len(report.issues) == 3
        assert report.issues[0].slide_index == 0
        assert report.issues[0].severity == "critical"
        assert report.issues[0].category == "overlap"
        assert report.issues[0].suggested_fix is not None
        assert report.issues[1].slide_index == 0
        assert report.issues[1].severity == "warning"
        assert report.issues[2].slide_index == 1
        assert report.issues[2].severity == "minor"

    def test_parse_empty_response(self):
        agent = self._get_agent_with_mock()
        report = agent._parse_response("")
        assert report.passed is True
        assert len(report.issues) == 0

    def test_parse_multiple_slides(self):
        agent = self._get_agent_with_mock()
        text = (
            "SLIDE 1: Title\n"
            "- [WARNING] alignment: Title not centered\n\n"
            "SLIDE 3: Timeline\n"
            "- [CRITICAL] overflow: Steps overflow slide boundary\n\n"
            "FAIL: 1 critical issues"
        )
        report = agent._parse_response(text)
        assert len(report.issues) == 2
        assert report.issues[0].slide_index == 0
        assert report.issues[1].slide_index == 2


class TestQAAgentInspect:
    """Test the inspect method with mocked API."""

    def test_inspect_empty_slides(self):
        from agents.qa_agent import QAAgent

        with patch("agents.qa_agent.anthropic.Anthropic"):
            agent = QAAgent.__new__(QAAgent)
            agent.client = MagicMock()
            agent.model = "test"
            agent.serializer = MagicMock()
            agent._system_prompt = "test"

        report = agent.inspect([], [])
        assert report.passed is True
        assert report.summary == "No slides to inspect."

    def test_inspect_calls_api(self):
        from agents.qa_agent import QAAgent, SlideImage

        with patch("agents.qa_agent.anthropic.Anthropic"):
            agent = QAAgent.__new__(QAAgent)
            agent.client = MagicMock()
            agent.model = "test"
            agent.serializer = MagicMock()
            agent._system_prompt = "test"

        # Mock API response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="SLIDE 1: Test\n\nPASS: All good")]
        agent.client.messages.create.return_value = mock_response

        slide_images = [
            SlideImage(
                slide_index=0,
                image_path="/nonexistent/slide.jpg",
                dsl_text="# Test\n@type: title",
            )
        ]
        report = agent.inspect(slide_images, [])
        assert agent.client.messages.create.called
        assert report.passed is True


# ═══════════════════════════════════════════════════════════════════════
# Index Curator Tests
# ═══════════════════════════════════════════════════════════════════════


class TestValidateDomain:
    def test_valid_domains(self):
        from agents.index_curator import _validate_domain

        assert _validate_domain("metrics") == "metrics"
        assert _validate_domain("strategy") == "strategy"
        assert _validate_domain("RISK") == "risk"
        assert _validate_domain("  timeline  ") == "timeline"

    def test_invalid_domain_falls_back(self):
        from agents.index_curator import _validate_domain

        assert _validate_domain("unknown") == "overview"
        assert _validate_domain("") == "overview"
        assert _validate_domain("foobar") == "overview"


class TestParseJson:
    def test_plain_json(self):
        from agents.index_curator import _parse_json

        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_fences(self):
        from agents.index_curator import _parse_json

        result = _parse_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_json_array(self):
        from agents.index_curator import _parse_json

        result = _parse_json('[{"a": 1}, {"a": 2}]')
        assert isinstance(result, list)
        assert len(result) == 2

    def test_invalid_json_returns_empty(self):
        from agents.index_curator import _parse_json

        result = _parse_json("not json at all")
        assert result == {}


class TestIndexCuratorEnrichDeck:
    """Test deck enrichment with mocked API."""

    def _get_curator_with_mock(self, response_text: str):
        from agents.index_curator import IndexCuratorAgent

        with patch("agents.index_curator.anthropic.Anthropic"):
            curator = IndexCuratorAgent.__new__(IndexCuratorAgent)
            curator.client = MagicMock()
            curator.model = "test"
            curator.serializer = MagicMock()
            curator.serializer.serialize.return_value = "# Test\n@type: title"
            curator.serializer.serialize_slide.return_value = "# Test\n@type: title"
            curator._system_prompt = "test"

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=response_text)]
        curator.client.messages.create.return_value = mock_response
        return curator

    def test_enrich_deck(self):
        response = (
            '{"narrative_summary": "Q3 review", '
            '"audience": "leadership", '
            '"purpose": "quarterly update", '
            '"topic_tags": ["platform", "metrics"]}'
        )
        curator = self._get_curator_with_mock(response)

        parser = SlideDSLParser()
        sample_path = Path(__file__).parent.parent / "docs" / "examples" / "sample.sdsl"
        presentation = parser.parse_file(str(sample_path))

        enrichment = curator.enrich_deck(presentation)
        assert enrichment.narrative_summary == "Q3 review"
        assert enrichment.audience == "leadership"
        assert enrichment.purpose == "quarterly update"
        assert "platform" in enrichment.topic_tags

    def test_enrich_slide(self):
        response = (
            '{"semantic_summary": "3 KPI metrics", '
            '"topic_tags": ["pipeline", "uptime"], '
            '"content_domain": "metrics"}'
        )
        curator = self._get_curator_with_mock(response)

        slide = SlideNode(
            slide_name="Metrics",
            slide_type=SlideType.STAT_CALLOUT,
            background=BackgroundType.LIGHT,
        )
        enrichment = curator.enrich_slide(slide, "Q3 review deck")
        assert enrichment.semantic_summary == "3 KPI metrics"
        assert enrichment.content_domain == "metrics"

    def test_enrich_slide_invalid_domain_falls_back(self):
        response = (
            '{"semantic_summary": "test", '
            '"topic_tags": [], '
            '"content_domain": "invalid_domain"}'
        )
        curator = self._get_curator_with_mock(response)

        slide = SlideNode(
            slide_name="Test",
            slide_type=SlideType.BULLET_POINTS,
            background=BackgroundType.LIGHT,
        )
        enrichment = curator.enrich_slide(slide, "test")
        assert enrichment.content_domain == "overview"

    def test_enrich_slides_batch(self):
        response = (
            '[{"semantic_summary": "title slide", '
            '"topic_tags": ["intro"], "content_domain": "overview"}, '
            '{"semantic_summary": "metrics slide", '
            '"topic_tags": ["kpi"], "content_domain": "metrics"}]'
        )
        curator = self._get_curator_with_mock(response)

        slides = [
            SlideNode(
                slide_name="Title", slide_type=SlideType.TITLE, background=BackgroundType.DARK
            ),
            SlideNode(
                slide_name="Metrics",
                slide_type=SlideType.STAT_CALLOUT,
                background=BackgroundType.LIGHT,
            ),
        ]
        enrichments = curator.enrich_slides_batch(slides, "Q3 deck")
        assert len(enrichments) == 2
        assert enrichments[0].content_domain == "overview"
        assert enrichments[1].content_domain == "metrics"

    def test_enrich_slides_batch_empty(self):
        from agents.index_curator import IndexCuratorAgent

        with patch("agents.index_curator.anthropic.Anthropic"):
            curator = IndexCuratorAgent.__new__(IndexCuratorAgent)
            curator.client = MagicMock()
            curator.model = "test"
            curator.serializer = MagicMock()
            curator._system_prompt = "test"

        result = curator.enrich_slides_batch([], "context")
        assert result == []

    def test_enrich_element(self):
        response = (
            '{"semantic_summary": "Pipeline uptime at 94%", '
            '"topic_tags": ["pipeline", "uptime"]}'
        )
        curator = self._get_curator_with_mock(response)

        element = {"type": "stat", "value": "94%", "label": "Pipeline Uptime"}
        enrichment = curator.enrich_element(element, "metrics slide")
        assert "94%" in enrichment.semantic_summary
        assert "pipeline" in enrichment.topic_tags

    def test_enrich_elements_batch(self):
        response = (
            '[{"semantic_summary": "uptime metric", "topic_tags": ["uptime"]}, '
            '{"semantic_summary": "throughput metric", "topic_tags": ["throughput"]}]'
        )
        curator = self._get_curator_with_mock(response)

        elements = [
            {"type": "stat", "value": "94%"},
            {"type": "stat", "value": "3.2B"},
        ]
        enrichments = curator.enrich_elements_batch(elements, "metrics slide")
        assert len(enrichments) == 2


# ═══════════════════════════════════════════════════════════════════════
# Image Conversion Tests (mocked subprocess)
# ═══════════════════════════════════════════════════════════════════════


class TestPptxToImages:
    def test_pptx_to_images_no_soffice(self, tmp_path):
        from agents.qa_agent import pptx_to_images

        dummy_pptx = tmp_path / "test.pptx"
        dummy_pptx.write_bytes(b"fake")

        with patch("agents.qa_agent.subprocess.run", side_effect=FileNotFoundError):
            result = pptx_to_images(dummy_pptx, tmp_path)
            assert result == []

    def test_pptx_to_images_success(self, tmp_path):
        from agents.qa_agent import pptx_to_images

        dummy_pptx = tmp_path / "test.pptx"
        dummy_pptx.write_bytes(b"fake")

        def mock_run(cmd, **kwargs):
            if "soffice" in cmd:
                # Create a fake PDF
                (tmp_path / "test.pdf").write_bytes(b"fake pdf")
            elif "pdftoppm" in cmd:
                # Create fake slide images
                (tmp_path / "slide-1.jpg").write_bytes(b"fake img 1")
                (tmp_path / "slide-2.jpg").write_bytes(b"fake img 2")
            return MagicMock(returncode=0)

        with patch("agents.qa_agent.subprocess.run", side_effect=mock_run):
            result = pptx_to_images(dummy_pptx, tmp_path)
            assert len(result) == 2
            assert result[0].name == "slide-1.jpg"
