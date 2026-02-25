"""
src/requirements/validator.py — DSL-level requirements validation.

Checks generated DSL text against PresentationRequirements using pure
keyword/structural analysis — no LLM call needed. Catches obvious gaps
cheaply before the more expensive vision QA pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.requirements.parser import PresentationRequirements


@dataclass
class RequirementCoverage:
    """Coverage status for a single requirement."""

    requirement_text: str
    satisfied: bool
    evidence_slides: list[int] = field(default_factory=list)
    gap_description: str = ""


@dataclass
class ValidationReport:
    """Full validation result after checking DSL against requirements."""

    coverage_score: float  # 0.0 – 1.0
    coverages: list[RequirementCoverage] = field(default_factory=list)
    critical_gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed: bool = True  # True if no critical gaps


class RequirementsValidator:
    """
    Validates generated DSL text against structured PresentationRequirements.

    Runs pure structural/keyword checks — no API calls.

    Checks performed:
      - Required sections present in slide titles
      - Required slide types declared in DSL
      - Key messages present (keyword matching across all slides)
      - Audience-appropriate depth indicators
      - Source attribution on data slides (stat_callout, comparison, timeline)
    """

    # Slide types expected to have @source lines
    _DATA_SLIDE_TYPES = {"stat_callout", "comparison", "timeline", "chart", "data"}

    # Patterns for extracting slide type declarations
    _SLIDE_TYPE_RE = re.compile(r"^\s*type\s*:\s*(\w+)", re.MULTILINE)

    # Patterns for slide titles
    _SLIDE_TITLE_RE = re.compile(r"^\s*title\s*:\s*(.+)", re.MULTILINE)

    # Source line pattern
    _SOURCE_RE = re.compile(r"@source\s*:", re.IGNORECASE)

    def validate(
        self,
        dsl_text: str,
        requirements: PresentationRequirements,
    ) -> ValidationReport:
        """
        Validate DSL text against requirements.

        Args:
            dsl_text: The generated SlideForge DSL text.
            requirements: Extracted requirements to check against.

        Returns:
            ValidationReport with coverage score, gaps, and warnings.
        """
        coverages: list[RequirementCoverage] = []
        critical_gaps: list[str] = []
        warnings: list[str] = []

        dsl_lower = dsl_text.lower()
        slide_types = [m.lower() for m in self._SLIDE_TYPE_RE.findall(dsl_text)]
        slide_titles = [m.strip().lower() for m in self._SLIDE_TITLE_RE.findall(dsl_text)]

        # Check must-have sections (by title keyword matching)
        for section in requirements.must_have_sections:
            cov = self._check_section_present(section, slide_titles)
            coverages.append(cov)
            if not cov.satisfied:
                critical_gaps.append(f"Required section '{section}' not found in any slide title")

        # Check must-have slide types
        for required_type in requirements.must_have_slide_types:
            cov = self._check_slide_type_present(required_type, slide_types)
            coverages.append(cov)
            if not cov.satisfied:
                critical_gaps.append(f"Required slide type '{required_type}' not present in deck")

        # Check key messages (keyword presence across full DSL)
        for message in requirements.key_messages:
            cov = self._check_key_message(message, dsl_lower, slide_titles)
            coverages.append(cov)
            if not cov.satisfied:
                warnings.append(f"Key message not found in DSL: '{message[:80]}'")

        # Check data slides have @source
        if self._has_data_slides(slide_types):
            source_count = len(self._SOURCE_RE.findall(dsl_text))
            data_slide_count = sum(1 for t in slide_types if t in self._DATA_SLIDE_TYPES)
            if source_count == 0 and data_slide_count > 0:
                cov = RequirementCoverage(
                    requirement_text="Data slides must have @source attribution",
                    satisfied=False,
                    gap_description=f"{data_slide_count} data slide(s) with no @source lines",
                )
                coverages.append(cov)
                critical_gaps.append(
                    f"{data_slide_count} data slide(s) missing @source attribution"
                )

        # Check audience depth — warn if C-suite but no exec_summary
        persona = requirements.audience_persona
        if persona.seniority == "c-suite" and "exec_summary" not in slide_types:
            exec_title_found = any(
                "executive summary" in t or "exec summary" in t or "exec_summary" in t
                for t in slide_titles
            )
            if not exec_title_found:
                warnings.append("C-suite audience expects an executive summary slide — none found")

        # Check forbidden elements are absent
        for forbidden in persona.forbidden_elements:
            if forbidden.lower() in dsl_lower:
                warnings.append(
                    f"Forbidden element '{forbidden}' found in DSL (audience restriction)"
                )

        # Calculate coverage score
        total = len(coverages)
        if total == 0:
            score = 1.0
        else:
            satisfied = sum(1 for c in coverages if c.satisfied)
            score = satisfied / total

        passed = len(critical_gaps) == 0

        return ValidationReport(
            coverage_score=score,
            coverages=coverages,
            critical_gaps=critical_gaps,
            warnings=warnings,
            passed=passed,
        )

    # ── Internal check helpers ──────────────────────────────────────

    def _check_section_present(self, section: str, slide_titles: list[str]) -> RequirementCoverage:
        """Check if a required section name appears in any slide title."""
        section_lower = section.lower()
        keywords = section_lower.split()
        evidence = []

        for i, title in enumerate(slide_titles):
            if any(kw in title for kw in keywords):
                evidence.append(i)

        return RequirementCoverage(
            requirement_text=f"Section: {section}",
            satisfied=len(evidence) > 0,
            evidence_slides=evidence,
            gap_description="" if evidence else f"No slide title matches '{section}'",
        )

    def _check_slide_type_present(
        self, required_type: str, slide_types: list[str]
    ) -> RequirementCoverage:
        """Check if a required slide type appears in the DSL."""
        required_lower = required_type.lower().replace(" ", "_")
        # Normalize common aliases
        aliases: dict[str, list[str]] = {
            "exec_summary": ["exec_summary", "executive_summary"],
            "next_steps": ["next_steps", "next_step", "closing"],
            "title": ["title"],
            "closing": ["closing", "end_slide", "thank_you"],
        }
        check_types = aliases.get(required_lower, [required_lower])
        evidence = [
            i for i, t in enumerate(slide_types) if any(t == check for check in check_types)
        ]
        return RequirementCoverage(
            requirement_text=f"Slide type: {required_type}",
            satisfied=len(evidence) > 0,
            evidence_slides=evidence,
            gap_description="" if evidence else f"No slide with type '{required_type}'",
        )

    def _check_key_message(
        self, message: str, dsl_lower: str, slide_titles: list[str]
    ) -> RequirementCoverage:
        """Check if a key message's keywords appear in the DSL."""
        # Extract content words (3+ chars, not stopwords)
        _STOPWORDS = {"the", "and", "for", "that", "this", "with", "from", "are", "will"}
        words = [
            w.lower().strip(".,;:!?\"'")
            for w in message.split()
            if len(w) >= 3 and w.lower() not in _STOPWORDS
        ]

        if not words:
            return RequirementCoverage(
                requirement_text=message,
                satisfied=True,  # nothing specific to check
                gap_description="",
            )

        # Check if majority of keywords appear somewhere in DSL
        found_words = [w for w in words if w in dsl_lower]
        coverage_ratio = len(found_words) / len(words)
        satisfied = coverage_ratio >= 0.5

        evidence = []
        for i, title in enumerate(slide_titles):
            if any(w in title for w in found_words):
                evidence.append(i)

        return RequirementCoverage(
            requirement_text=message,
            satisfied=satisfied,
            evidence_slides=evidence,
            gap_description=(
                ""
                if satisfied
                else f"Keywords not found: {', '.join(w for w in words if w not in dsl_lower)}"
            ),
        )

    def _has_data_slides(self, slide_types: list[str]) -> bool:
        """Return True if any slide is a data slide type."""
        return any(t in self._DATA_SLIDE_TYPES for t in slide_types)
