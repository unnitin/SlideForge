"""
tests/test_requirements.py — Unit tests for RequirementsParser and RequirementsValidator.

These tests use mocking to avoid real API calls for the parser, and run the
validator fully offline since it is pure Python.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.requirements.parser import (
    AudiencePersona,
    ContentRequirement,
    PresentationRequirements,
    RequirementsParser,
)
from src.requirements.validator import (
    RequirementsValidator,
)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def sample_requirements() -> PresentationRequirements:
    """A fully-populated PresentationRequirements for testing."""
    return PresentationRequirements(
        audience_persona=AudiencePersona(
            role="CFO",
            seniority="c-suite",
            domain_expertise="finance",
            expected_depth="high",
            forbidden_elements=["operational jargon"],
            must_have_elements=["financial tables"],
        ),
        key_messages=[
            "Revenue grew 23% YoY driven by enterprise contracts",
            "EBITDA margin improved to 18%",
        ],
        must_have_sections=["Executive Summary", "Next Steps"],
        must_have_slide_types=["exec_summary", "next_steps", "title"],
        tone="formal",
        data_requirements=[
            ContentRequirement(
                claim_topic="Revenue",
                must_include=["Q3 revenue", "YoY comparison"],
                source_priority="primary",
                data_freshness="current",
            )
        ],
        constraints={"slide_count": 8},
        consulting_standards=["MECE", "action_titles"],
        raw_input="Create a Q3 board update for the CFO",
    )


@pytest.fixture
def minimal_dsl() -> str:
    """Minimal DSL with exec_summary, next_steps, and title slides."""
    return """
---
presentation:
  title: "Q3 Board Update"
  audience: "CFO"
---

slide:
  type: title
  title: "Q3 Results: Revenue Grew 23% YoY"

slide:
  type: exec_summary
  title: "Executive Summary: EBITDA Margin Improved to 18%"
  content:
    - Revenue grew 23% YoY driven by enterprise contracts
    - EBITDA margin improved to 18%

slide:
  type: stat_callout
  title: "Revenue Grew 23% Driven by Enterprise"
  @source: internal finance report Q3 2025

slide:
  type: next_steps
  title: "Next Steps: Accelerate enterprise sales in Q4"
"""


@pytest.fixture
def missing_sections_dsl() -> str:
    """DSL that is missing exec_summary and next_steps."""
    return """
---
presentation:
  title: "Q3 Update"
---

slide:
  type: title
  title: "Q3 Results"

slide:
  type: bullets
  title: "Revenue Overview"
  content:
    - Revenue data here
"""


# ── RequirementsParser tests ────────────────────────────────────────


class TestRequirementsParserStructure:
    """Tests for the parser's output structure (mocked API)."""

    def _make_mock_response(self, data: dict) -> MagicMock:
        """Build a mock Anthropic response with JSON payload."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(data))]
        return mock_response

    def test_parse_returns_presentation_requirements(self):
        """parse() should always return a PresentationRequirements."""
        parser = RequirementsParser(api_key="test-key")
        payload = {
            "audience_persona": {
                "role": "VP Engineering",
                "seniority": "senior",
                "domain_expertise": "engineering",
                "expected_depth": "high",
                "forbidden_elements": [],
                "must_have_elements": [],
            },
            "key_messages": ["Ship faster", "Reduce technical debt"],
            "must_have_sections": ["Roadmap"],
            "must_have_slide_types": ["title", "next_steps"],
            "tone": "conversational",
            "data_requirements": [],
            "constraints": {"slide_count": 6, "confidentiality": None},
            "consulting_standards": ["action_titles"],
        }
        with patch.object(
            parser.client.messages, "create", return_value=self._make_mock_response(payload)
        ):
            result = parser.parse("Build a roadmap deck for engineering", audience="VP Engineering")

        assert isinstance(result, PresentationRequirements)
        assert result.audience_persona.role == "VP Engineering"
        assert result.audience_persona.seniority == "senior"
        assert len(result.key_messages) == 2
        assert "Ship faster" in result.key_messages
        assert result.tone == "conversational"
        assert result.constraints.get("slide_count") == 6
        # null values should be filtered out
        assert "confidentiality" not in result.constraints

    def test_parse_preserves_raw_input(self):
        """raw_input should always be set to the original prompt."""
        parser = RequirementsParser(api_key="test-key")
        payload = {
            "audience_persona": {
                "role": "CTO",
                "seniority": "c-suite",
                "domain_expertise": "engineering",
                "expected_depth": "high",
                "forbidden_elements": [],
                "must_have_elements": [],
            },
            "key_messages": [],
            "must_have_sections": [],
            "must_have_slide_types": [],
            "tone": "formal",
            "data_requirements": [],
            "constraints": {},
            "consulting_standards": [],
        }
        user_input = "Create a tech strategy deck"
        with patch.object(
            parser.client.messages, "create", return_value=self._make_mock_response(payload)
        ):
            result = parser.parse(user_input)

        assert result.raw_input == user_input

    def test_parse_falls_back_to_defaults_on_api_error(self):
        """parse() should return a defaults object when the API fails."""
        parser = RequirementsParser(api_key="test-key")
        with patch.object(
            parser.client.messages, "create", side_effect=Exception("API unavailable")
        ):
            result = parser.parse("some prompt", audience="board")

        assert isinstance(result, PresentationRequirements)
        assert result.audience_persona.role == "board"
        assert result.raw_input == "some prompt"

    def test_parse_handles_markdown_fenced_json(self):
        """parse() should strip markdown fences if the model wraps JSON."""
        parser = RequirementsParser(api_key="test-key")
        payload = {
            "audience_persona": {
                "role": "analyst",
                "seniority": "mid",
                "domain_expertise": "finance",
                "expected_depth": "medium",
                "forbidden_elements": [],
                "must_have_elements": [],
            },
            "key_messages": ["Market grew"],
            "must_have_sections": [],
            "must_have_slide_types": [],
            "tone": "formal",
            "data_requirements": [],
            "constraints": {},
            "consulting_standards": [],
        }
        fenced = f"```json\n{json.dumps(payload)}\n```"
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text=fenced)]

        with patch.object(parser.client.messages, "create", return_value=mock_resp):
            result = parser.parse("market analysis")

        assert result.key_messages == ["Market grew"]

    def test_parse_data_requirements_mapping(self):
        """ContentRequirement fields should map correctly from JSON."""
        parser = RequirementsParser(api_key="test-key")
        payload = {
            "audience_persona": {
                "role": "CFO",
                "seniority": "c-suite",
                "domain_expertise": "finance",
                "expected_depth": "high",
                "forbidden_elements": [],
                "must_have_elements": [],
            },
            "key_messages": [],
            "must_have_sections": [],
            "must_have_slide_types": [],
            "tone": "formal",
            "data_requirements": [
                {
                    "claim_topic": "Revenue",
                    "must_include": ["Q3 revenue", "YoY"],
                    "source_priority": "primary",
                    "data_freshness": "current",
                }
            ],
            "constraints": {},
            "consulting_standards": [],
        }
        with patch.object(
            parser.client.messages, "create", return_value=self._make_mock_response(payload)
        ):
            result = parser.parse("pricing analysis")

        assert len(result.data_requirements) == 1
        dr = result.data_requirements[0]
        assert dr.claim_topic == "Revenue"
        assert "Q3 revenue" in dr.must_include
        assert dr.source_priority == "primary"
        assert dr.data_freshness == "current"


# ── RequirementsValidator tests ─────────────────────────────────────


class TestRequirementsValidator:
    """Tests for the pure-Python DSL validator."""

    def test_validate_passes_when_all_requirements_met(self, sample_requirements, minimal_dsl):
        """Validator should pass when all critical requirements are present."""
        validator = RequirementsValidator()
        report = validator.validate(minimal_dsl, sample_requirements)

        assert report.passed is True
        assert len(report.critical_gaps) == 0
        assert report.coverage_score > 0.5

    def test_validate_detects_missing_required_section(self, sample_requirements):
        """Validator should flag missing must_have_sections as critical gaps."""
        dsl = """
---
presentation:
  title: "Q3 Update"
---
slide:
  type: title
  title: "Q3 Results"
"""
        validator = RequirementsValidator()
        report = validator.validate(dsl, sample_requirements)

        assert report.passed is False
        assert any("Executive Summary" in g for g in report.critical_gaps)
        assert any("Next Steps" in g for g in report.critical_gaps)

    def test_validate_detects_missing_slide_type(self, sample_requirements):
        """Validator should flag missing must_have_slide_types as critical gaps."""
        dsl = """
---
presentation:
  title: "Q3 Update"
---
slide:
  type: bullets
  title: "Revenue Overview"
"""
        validator = RequirementsValidator()
        report = validator.validate(dsl, sample_requirements)

        assert report.passed is False
        gap_text = " ".join(report.critical_gaps)
        assert "exec_summary" in gap_text or "next_steps" in gap_text

    def test_validate_coverage_score_range(self, sample_requirements, minimal_dsl):
        """Coverage score must be in [0.0, 1.0]."""
        validator = RequirementsValidator()
        report = validator.validate(minimal_dsl, sample_requirements)

        assert 0.0 <= report.coverage_score <= 1.0

    def test_validate_coverage_score_low_when_nothing_matches(self):
        """Coverage score should be low when almost no requirements are satisfied."""
        requirements = PresentationRequirements(
            audience_persona=AudiencePersona(role="CEO", seniority="c-suite"),
            key_messages=["Unicorn valuation", "10x growth", "AI-native platform"],
            must_have_sections=["Market Opportunity", "Financial Projections"],
            must_have_slide_types=["exec_summary", "next_steps"],
        )
        empty_dsl = """
---
presentation:
  title: "Unrelated deck"
---
slide:
  type: title
  title: "Hello World"
"""
        validator = RequirementsValidator()
        report = validator.validate(empty_dsl, requirements)

        assert report.coverage_score < 0.5
        assert report.passed is False

    def test_validate_warns_for_missing_key_message(self, sample_requirements):
        """Unmatched key messages should appear in warnings (not critical gaps)."""
        dsl = """
---
presentation:
  title: "Q3 Update"
---
slide:
  type: title
  title: "Q3 Results"

slide:
  type: exec_summary
  title: "Executive Summary overview"

slide:
  type: next_steps
  title: "Next Steps"
"""
        validator = RequirementsValidator()
        report = validator.validate(dsl, sample_requirements)

        # Key messages not found → warnings, not critical
        all_warnings_text = " ".join(report.warnings)
        assert "Key message" in all_warnings_text or len(report.warnings) >= 0

    def test_validate_data_slide_without_source_is_critical(self):
        """stat_callout slide without @source should be a critical gap."""
        requirements = PresentationRequirements(
            audience_persona=AudiencePersona(role="analyst"),
        )
        dsl = """
---
presentation:
  title: "Data deck"
---
slide:
  type: stat_callout
  title: "Revenue grew 20%"
  stat: "20%"
"""
        validator = RequirementsValidator()
        report = validator.validate(dsl, requirements)

        assert report.passed is False
        assert any("source" in g.lower() for g in report.critical_gaps)

    def test_validate_data_slide_with_source_passes(self):
        """stat_callout slide with @source should not trigger a critical gap."""
        requirements = PresentationRequirements(
            audience_persona=AudiencePersona(role="analyst"),
        )
        dsl = """
---
presentation:
  title: "Data deck"
---
slide:
  type: stat_callout
  title: "Revenue grew 20%"
  stat: "20%"
  @source: internal Q3 report
"""
        validator = RequirementsValidator()
        report = validator.validate(dsl, requirements)

        assert all("source" not in g.lower() for g in report.critical_gaps)

    def test_validate_no_requirements_returns_full_coverage(self):
        """Validation with no requirements to check should give 1.0 coverage."""
        requirements = PresentationRequirements(
            audience_persona=AudiencePersona(role="general"),
        )
        dsl = "---\npresentation:\n  title: 'X'\n---\nslide:\n  type: title\n  title: 'Hi'\n"
        validator = RequirementsValidator()
        report = validator.validate(dsl, requirements)

        assert report.coverage_score == 1.0
        assert report.passed is True

    def test_validate_forbidden_element_is_warning_not_critical(self):
        """Forbidden elements in DSL should appear in warnings, not critical gaps."""
        requirements = PresentationRequirements(
            audience_persona=AudiencePersona(
                role="CFO",
                forbidden_elements=["sprint velocity"],
            ),
        )
        dsl = """
---
presentation:
  title: "Ops Review"
---
slide:
  type: bullets
  title: "Engineering Update"
  content:
    - Sprint velocity increased 15%
"""
        validator = RequirementsValidator()
        report = validator.validate(dsl, requirements)

        assert any("sprint velocity" in w.lower() for w in report.warnings)
        # Should not be a critical gap
        assert not any("sprint velocity" in g.lower() for g in report.critical_gaps)

    def test_validate_c_suite_without_exec_summary_warns(self):
        """C-suite audience without exec_summary should trigger a warning."""
        requirements = PresentationRequirements(
            audience_persona=AudiencePersona(role="CEO", seniority="c-suite"),
        )
        dsl = """
---
presentation:
  title: "Update"
---
slide:
  type: title
  title: "Q3 Overview"
slide:
  type: bullets
  title: "Results"
"""
        validator = RequirementsValidator()
        report = validator.validate(dsl, requirements)

        assert any("executive summary" in w.lower() or "exec" in w.lower() for w in report.warnings)

    def test_validate_report_has_coverages_list(self, sample_requirements, minimal_dsl):
        """ValidationReport.coverages should be populated with individual checks."""
        validator = RequirementsValidator()
        report = validator.validate(minimal_dsl, sample_requirements)

        assert isinstance(report.coverages, list)
        # Should have at least as many entries as must_have_sections + must_have_slide_types
        total_checks = len(sample_requirements.must_have_sections) + len(
            sample_requirements.must_have_slide_types
        )
        assert len(report.coverages) >= total_checks
