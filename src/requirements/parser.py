"""
src/requirements/parser.py — Structured requirements extraction from natural language.

Parses a user's NL prompt into a PresentationRequirements object that captures
audience persona, key messages, must-have sections/slide-types, tone, data
requirements, and constraints. This structured representation is carried through
the full pipeline to enable requirements-aware generation and validation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)


@dataclass
class AudiencePersona:
    """Structured description of the target audience."""

    role: str = "general"
    seniority: str = "senior"  # "junior", "mid", "senior", "c-suite"
    domain_expertise: str = "general"  # e.g. "finance", "engineering", "operations"
    expected_depth: str = "medium"  # "high", "medium", "low"
    forbidden_elements: list[str] = field(default_factory=list)
    must_have_elements: list[str] = field(default_factory=list)


@dataclass
class ContentRequirement:
    """A specific data or claim requirement for a slide or section."""

    claim_topic: str
    must_include: list[str] = field(default_factory=list)
    source_priority: str = "primary"  # "primary" | "supporting"
    data_freshness: str = "any"  # "current", "recent", "any"


@dataclass
class PresentationRequirements:
    """
    Fully structured requirements for a presentation.

    Extracted from the user's NL prompt. Carried through generation,
    validation, and QA as the source-of-truth for what the deck must do.
    """

    audience_persona: AudiencePersona = field(default_factory=AudiencePersona)
    key_messages: list[str] = field(default_factory=list)
    must_have_sections: list[str] = field(default_factory=list)
    must_have_slide_types: list[str] = field(default_factory=list)
    tone: str = "formal"  # "formal", "conversational", "urgent"
    data_requirements: list[ContentRequirement] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    consulting_standards: list[str] = field(default_factory=list)
    raw_input: str = ""


class RequirementsParser:
    """
    Extracts structured PresentationRequirements from a natural language prompt.

    Uses a cost-optimized model (Haiku) for fast, cheap structured extraction.
    Falls back to a best-effort defaults object on any API error.
    """

    _SYSTEM_PROMPT = """\
You are a requirements analyst for management consulting presentations.
Given a natural language request, extract structured presentation requirements.

Return ONLY a valid JSON object with this exact schema:
{
  "audience_persona": {
    "role": "<job title or role>",
    "seniority": "<junior|mid|senior|c-suite>",
    "domain_expertise": "<domain e.g. finance, engineering, operations, general>",
    "expected_depth": "<high|medium|low>",
    "forbidden_elements": ["<elements to avoid>"],
    "must_have_elements": ["<required design elements>"]
  },
  "key_messages": ["<message 1>", "<message 2>"],
  "must_have_sections": ["<section name>"],
  "must_have_slide_types": ["<slide type e.g. exec_summary, next_steps, title, closing>"],
  "tone": "<formal|conversational|urgent>",
  "data_requirements": [
    {
      "claim_topic": "<topic>",
      "must_include": ["<data point or claim>"],
      "source_priority": "<primary|supporting>",
      "data_freshness": "<current|recent|any>"
    }
  ],
  "constraints": {
    "slide_count": <number or null>,
    "confidentiality": "<public|internal|confidential|null>",
    "format": "<format or null>"
  },
  "consulting_standards": ["<standard e.g. MECE, action_titles, scqa>"]
}

Rules:
- Be specific about audience role and depth expectations
- Extract ALL key messages the deck must communicate
- Include "exec_summary" and "next_steps" as must_have_slide_types for any board/exec audience
- Infer consulting standards from context (e.g. "board update" → SCQA, action_titles)
- If a number of slides is mentioned, put it in constraints.slide_count
- Return only the JSON object, no markdown fences, no explanation
"""

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        api_key: Optional[str] = None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def parse(
        self,
        user_input: str,
        audience: str = "general",
        source_documents: Optional[list[str]] = None,
    ) -> PresentationRequirements:
        """
        Extract structured requirements from a natural language prompt.

        Args:
            user_input: The user's NL description of the desired presentation.
            audience: Explicit audience hint (may be overridden by LLM analysis).
            source_documents: Optional source documents for context.

        Returns:
            PresentationRequirements with all extracted fields.
        """
        prompt = self._build_prompt(user_input, audience, source_documents)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=self._SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_json = response.content[0].text.strip()
            return self._parse_response(raw_json, user_input)
        except Exception as e:
            logger.warning("RequirementsParser API call failed: %s — using defaults", e)
            return self._defaults(user_input, audience)

    def _build_prompt(
        self,
        user_input: str,
        audience: str,
        source_documents: Optional[list[str]],
    ) -> str:
        parts = [
            f"Presentation request: {user_input}",
            f"Stated audience: {audience}",
        ]
        if source_documents:
            parts.append(f"Source documents provided: {len(source_documents)} document(s)")
        return "\n".join(parts)

    def _parse_response(self, raw_json: str, raw_input: str) -> PresentationRequirements:
        """Parse the LLM JSON response into a PresentationRequirements object."""
        # Strip markdown fences if the model added them
        if raw_json.startswith("```"):
            lines = raw_json.split("\n")
            lines = [line for line in lines[1:] if line.strip() != "```"]
            raw_json = "\n".join(lines)

        data = json.loads(raw_json)

        persona_data = data.get("audience_persona", {})
        persona = AudiencePersona(
            role=persona_data.get("role", "general"),
            seniority=persona_data.get("seniority", "senior"),
            domain_expertise=persona_data.get("domain_expertise", "general"),
            expected_depth=persona_data.get("expected_depth", "medium"),
            forbidden_elements=persona_data.get("forbidden_elements", []),
            must_have_elements=persona_data.get("must_have_elements", []),
        )

        data_reqs = []
        for dr in data.get("data_requirements", []):
            data_reqs.append(
                ContentRequirement(
                    claim_topic=dr.get("claim_topic", ""),
                    must_include=dr.get("must_include", []),
                    source_priority=dr.get("source_priority", "primary"),
                    data_freshness=dr.get("data_freshness", "any"),
                )
            )

        constraints = data.get("constraints", {})
        # Normalize null values from JSON
        constraints = {k: v for k, v in constraints.items() if v is not None}

        return PresentationRequirements(
            audience_persona=persona,
            key_messages=data.get("key_messages", []),
            must_have_sections=data.get("must_have_sections", []),
            must_have_slide_types=data.get("must_have_slide_types", []),
            tone=data.get("tone", "formal"),
            data_requirements=data_reqs,
            constraints=constraints,
            consulting_standards=data.get("consulting_standards", []),
            raw_input=raw_input,
        )

    @staticmethod
    def _defaults(user_input: str, audience: str) -> PresentationRequirements:
        """Return a minimal PresentationRequirements when parsing fails."""
        return PresentationRequirements(
            audience_persona=AudiencePersona(role=audience),
            raw_input=user_input,
        )
