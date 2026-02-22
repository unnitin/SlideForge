"""
agents/nl_to_dsl.py — Natural Language → SlideForge Translation Agent

Takes raw user input + retrieved design context and produces valid .sdsl.
This is the primary user-facing agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anthropic

from src.dsl.models import BrandConfig, PresentationNode
from src.dsl.parser import SlideForgeParser
from src.index.retriever import SearchResult


@dataclass
class GenerationContext:
    """Everything the agent needs to generate a deck."""

    user_input: str
    similar_slides: list[SearchResult] = field(default_factory=list)
    similar_decks: list[SearchResult] = field(default_factory=list)
    relevant_elements: list[SearchResult] = field(default_factory=list)
    brand: Optional[BrandConfig] = None
    template_layouts: Optional[list[str]] = None
    target_slide_count: Optional[int] = None
    output_format: str = "pptx"
    audience: str = "general"
    source_documents: Optional[list[str]] = None
    existing_dsl: Optional[str] = None


@dataclass
class GenerationResult:
    """Output from the NL-to-DSL agent."""

    dsl_text: str
    presentation: Optional[PresentationNode]  # parsed result (None if parse failed)
    confidence: float
    design_references: list[str]  # chunk_ids that influenced output
    reasoning: str
    parse_errors: list[str] = field(default_factory=list)


class NLToDSLAgent:
    """
    Translates natural language → SlideForge using Claude.

    The agent is retrieval-augmented: it receives proven designs from the
    design index as part of its prompt context.
    """

    MAX_RETRIES = 2

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250514",
        api_key: Optional[str] = None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.parser = SlideForgeParser()
        self._system_prompt = self._load_system_prompt()

    def generate(self, context: GenerationContext) -> GenerationResult:
        """
        Generate SlideForge from natural language + context.

        Flow:
        1. Build prompt with retrieved context
        2. Call Claude to generate DSL
        3. Parse and validate
        4. Retry on parse failure (max 2 times)
        5. Return result with parsed presentation or errors
        """
        prompt = self._build_prompt(context)
        design_refs = [
            r.chunk_id
            for r in context.similar_slides + context.similar_decks + context.relevant_elements
        ]

        dsl_text = ""
        parse_errors: list[str] = []

        for attempt in range(1 + self.MAX_RETRIES):
            if attempt == 0:
                messages = [{"role": "user", "content": prompt}]
            else:
                # Retry with error feedback
                messages = [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": dsl_text},
                    {"role": "user", "content": self._retry_prompt(parse_errors)},
                ]

            response = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=self._system_prompt,
                messages=messages,
            )

            dsl_text = response.content[0].text.strip()

            # Strip markdown fences if the LLM wrapped them
            dsl_text = _strip_fences(dsl_text)

            # Try parsing
            try:
                presentation = self.parser.parse(dsl_text)
                if len(presentation.slides) == 0:
                    parse_errors.append("No slides found in output")
                    continue

                return GenerationResult(
                    dsl_text=dsl_text,
                    presentation=presentation,
                    confidence=self._estimate_confidence(presentation, context),
                    design_references=design_refs,
                    reasoning=f"Generated {len(presentation.slides)} slides on attempt {attempt + 1}",
                )

            except Exception as e:
                parse_errors.append(f"Parse error on attempt {attempt + 1}: {str(e)}")

        # All retries exhausted — return partial result
        return GenerationResult(
            dsl_text=dsl_text,
            presentation=None,
            confidence=0.0,
            design_references=design_refs,
            reasoning="Failed to generate valid DSL after retries",
            parse_errors=parse_errors,
        )

    # ── Prompt Building ────────────────────────────────────────────

    def _load_system_prompt(self) -> str:
        prompt_path = Path(__file__).parent / "prompts" / "nl_to_dsl.txt"
        return prompt_path.read_text(encoding="utf-8")

    def _build_prompt(self, ctx: GenerationContext) -> str:
        parts = [f"Create a presentation for:\n\n{ctx.user_input}"]

        # Source documents
        if ctx.source_documents:
            parts.append("\n## Source Material\n")
            for i, doc in enumerate(ctx.source_documents[:3]):  # limit to 3
                parts.append(f"### Document {i + 1}\n{doc[:5000]}\n")

        # Existing DSL (if editing)
        if ctx.existing_dsl:
            parts.append(f"\n## Existing Deck (modify this)\n```\n{ctx.existing_dsl}\n```\n")

        # Retrieved designs
        if ctx.similar_slides or ctx.similar_decks:
            parts.append("\n## Reference Designs\n")
            for r in ctx.similar_decks[:2]:
                parts.append(f"### Proven Deck Structure (quality: {r.quality_score:.0%})")
                parts.append(f"Summary: {r.semantic_summary}")
                if r.dsl_text:
                    parts.append(f"```\n{r.dsl_text[:500]}\n```\n")

            for r in ctx.similar_slides[:5]:
                parts.append(
                    f"### Proven Slide (type: {r.slide_type}, "
                    f"used {r.keep_count}x, quality: {r.quality_score:.0%})"
                )
                if r.dsl_text:
                    parts.append(f"```\n{r.dsl_text}\n```\n")

        return "\n".join(parts)

    def _retry_prompt(self, errors: list[str]) -> str:
        return (
            "The DSL you generated has issues:\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\n\nPlease fix these issues and output the complete, corrected .sdsl."
        )

    def _estimate_confidence(self, pres: PresentationNode, ctx: GenerationContext) -> float:
        """Rough confidence estimate based on structural quality."""
        score = 0.5

        # Has title and closing
        types = [s.slide_type.value for s in pres.slides]
        if "title" in types:
            score += 0.1
        if "closing" in types:
            score += 0.1

        # Varied slide types
        unique_types = len(set(types))
        if unique_types >= 3:
            score += 0.1
        if unique_types >= 5:
            score += 0.1

        # Close to target count
        if ctx.target_slide_count:
            diff = abs(len(pres.slides) - ctx.target_slide_count)
            if diff <= 1:
                score += 0.1

        return min(1.0, score)


# ── Helpers ────────────────────────────────────────────────────────


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```sdsl or ```)
        lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text
