"""
agents/qa_agent.py — Visual QA Inspection Agent (STUB)

TODO (Claude Code Phase 4):
    - Render slides to images (soffice + pdftoppm)
    - Send images to Claude Sonnet with vision
    - Parse QA issues from response
    - Implement fix-and-verify loop (max 3 cycles)

See specs/AGENT_SPEC.md for full contract.
See agents/prompts/qa_inspection.txt for system prompt.
"""

from dataclasses import dataclass, field
from typing import Optional

from src.dsl.models import SlideNode


@dataclass
class QAIssue:
    slide_index: int
    severity: str  # "critical", "warning", "minor"
    category: str  # "overlap", "overflow", "alignment", "contrast", "spacing", "content_missing"
    description: str
    suggested_fix: Optional[str] = None


@dataclass
class QAReport:
    issues: list[QAIssue] = field(default_factory=list)
    passed: bool = False
    summary: str = ""


class QAAgent:
    """Visual QA inspection agent — inspects rendered slides for issues."""

    def inspect(
        self,
        slide_image_paths: list[str],
        expected_slides: list[SlideNode],
    ) -> QAReport:
        raise NotImplementedError(
            "QA Agent not yet implemented. "
            "See specs/AGENT_SPEC.md and agents/prompts/qa_inspection.txt"
        )
