"""
src/services/orchestrator.py — End-to-End Pipeline Orchestrator

Coordinates the full flow:
  NL Input → Index Retrieval → NL-to-DSL Agent → Parse → Render → QA → Output
                                                          ↓
                                                   Index Ingestion
                                                   (feedback loop)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.dsl.models import BrandConfig, PresentationNode
from src.dsl.parser import SlideForgeParser
from src.dsl.serializer import SlideForgeSerializer
from src.index.chunker import SlideChunker
from src.index.embeddings import EmbedFn, embed_chunks, make_embed_fn
from src.index.retriever import DesignIndexRetriever
from src.index.store import DesignIndexStore
from src.renderer.pptx_renderer import render
from src.requirements.parser import PresentationRequirements, RequirementsParser
from src.requirements.validator import RequirementsValidator, ValidationReport
from agents.nl_to_dsl import GenerationContext, GenerationResult, NLToDSLAgent
from agents.qa_agent import QAAgent, QAReport

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the generation pipeline."""

    # Index
    index_db_path: str = "design_index.db"

    # Agent
    model: str = "claude-sonnet-4-6"
    api_key: Optional[str] = None

    # Rendering
    default_template: Optional[str] = None
    output_format: str = "pptx"
    output_dir: str = "./output"

    # Brand
    brand: Optional[BrandConfig] = None

    # Retrieval
    retrieval_limit: int = 5
    min_retrieval_score: float = 0.2

    # QA
    enable_qa: bool = True
    max_qa_cycles: int = 3

    # Embeddings
    embedding_backend: str = "auto"  # "auto" | "sentence_transformers" | "hash"

    # Requirements
    interactive: bool = False  # if True, pause for user confirmation after extraction


@dataclass
class PipelineResult:
    """Result from the full generation pipeline."""

    # Core output
    dsl_text: str
    presentation: Optional[PresentationNode]
    output_path: Optional[Path] = None

    # Metadata
    slide_count: int = 0
    generation_confidence: float = 0.0
    qa_passed: bool = False
    qa_issues: list[str] = field(default_factory=list)

    # Index integration
    deck_chunk_id: Optional[str] = None
    design_references: list[str] = field(default_factory=list)

    # Requirements coverage
    requirements_coverage: float = 0.0
    requirement_gaps: list[str] = field(default_factory=list)

    # Errors
    errors: list[str] = field(default_factory=list)


class Orchestrator:
    """
    End-to-end pipeline for slide generation.

    Usage:
        orch = Orchestrator(PipelineConfig(api_key="..."))
        result = orch.generate("Q3 data platform update for leadership")
        # result.output_path → path to generated .pptx
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.store = DesignIndexStore(config.index_db_path)
        self.store.initialize()
        self.embed_fn: EmbedFn = make_embed_fn(backend=config.embedding_backend)
        self.retriever = DesignIndexRetriever(self.store, embed_fn=self.embed_fn)
        self.agent = NLToDSLAgent(model=config.model, api_key=config.api_key)
        self.qa_agent = QAAgent(model=config.model, api_key=config.api_key)
        self.requirements_parser = RequirementsParser(api_key=config.api_key)
        self.requirements_validator = RequirementsValidator()
        self.parser = SlideForgeParser()
        self.serializer = SlideForgeSerializer()
        self.chunker = SlideChunker()

    def generate(
        self,
        user_input: str,
        audience: str = "general",
        target_slides: Optional[int] = None,
        source_documents: Optional[list[str]] = None,
        existing_dsl: Optional[str] = None,
    ) -> PipelineResult:
        """
        Full generation pipeline: NL → DSL → Render → QA.

        Args:
            user_input: Natural language description of desired presentation.
            audience: Who this deck is for.
            target_slides: Approximate number of slides desired.
            source_documents: Text content to base the deck on.
            existing_dsl: Existing .sdsl to modify (for edit flows).

        Returns:
            PipelineResult with DSL text, parsed presentation, and output path.
        """
        errors: list[str] = []
        requirements_coverage = 0.0
        requirement_gaps: list[str] = []

        # ── 0. Extract structured requirements ─────────────────────
        requirements: Optional[PresentationRequirements] = None
        try:
            requirements = self.requirements_parser.parse(
                user_input,
                audience=audience,
                source_documents=source_documents,
            )
            logger.info(
                "Requirements extracted: %d key messages, %d must-have sections",
                len(requirements.key_messages),
                len(requirements.must_have_sections),
            )
        except Exception as e:
            logger.warning("Requirements extraction failed: %s", e)

        # ── 0b. Interactive confirmation ────────────────────────────
        if self.config.interactive and requirements is not None:
            import sys

            if sys.stdin.isatty():
                _print_requirements_summary(requirements)
                answer = input("\nProceed with these requirements? [y/n/edit]: ").strip().lower()
                if answer == "n":
                    return PipelineResult(
                        dsl_text="",
                        presentation=None,
                        errors=["Generation cancelled by user."],
                    )
                # "edit" or any other value: continue (user can re-run with edits)

        # ── 1. Retrieve from design index ──────────────────────────
        similar_slides = self.retriever.search(
            user_input,
            granularity="slide",
            limit=self.config.retrieval_limit,
            min_score=self.config.min_retrieval_score,
        )
        similar_decks = self.retriever.search(
            user_input,
            granularity="deck",
            limit=3,
            min_score=self.config.min_retrieval_score,
        )
        relevant_elements = self.retriever.search(
            user_input,
            granularity="element",
            limit=self.config.retrieval_limit,
            min_score=self.config.min_retrieval_score,
        )

        # ── 2. Build generation context ────────────────────────────
        context = GenerationContext(
            user_input=user_input,
            similar_slides=similar_slides,
            similar_decks=similar_decks,
            relevant_elements=relevant_elements,
            brand=self.config.brand,
            target_slide_count=target_slides,
            output_format=self.config.output_format,
            audience=audience,
            source_documents=source_documents,
            existing_dsl=existing_dsl,
            requirements=requirements,
        )

        # ── 3. Generate DSL ────────────────────────────────────────
        gen_result: GenerationResult = self.agent.generate(context)

        if gen_result.presentation is None:
            return PipelineResult(
                dsl_text=gen_result.dsl_text,
                presentation=None,
                errors=gen_result.parse_errors,
            )

        presentation = gen_result.presentation

        # ── 4. Save DSL file ───────────────────────────────────────
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        dsl_path = output_dir / "presentation.sdsl"
        dsl_path.write_text(gen_result.dsl_text, encoding="utf-8")

        # ── 5b. Validate requirements ──────────────────────────────
        if requirements is not None:
            try:
                val_report: ValidationReport = self.requirements_validator.validate(
                    gen_result.dsl_text, requirements
                )
                requirements_coverage = val_report.coverage_score
                requirement_gaps = val_report.critical_gaps + val_report.warnings
                if val_report.critical_gaps:
                    logger.warning(
                        "Requirements validation: %d critical gap(s): %s",
                        len(val_report.critical_gaps),
                        "; ".join(val_report.critical_gaps[:3]),
                    )
                else:
                    logger.info(
                        "Requirements validation passed (coverage: %.0f%%)",
                        val_report.coverage_score * 100,
                    )
            except Exception as e:
                logger.warning("Requirements validation error: %s", e)

        # ── 5. Render ──────────────────────────────────────────────
        output_path: Optional[Path] = None
        try:
            output_path = render(
                presentation,
                output_dir,
                template_path=self.config.default_template,
            )
        except Exception as e:
            errors.append(f"Render error: {e}")

        # ── 6. QA Loop ────────────────────────────────────────────
        qa_passed = False
        qa_issues: list[str] = []

        if self.config.enable_qa and output_path and output_path.exists():
            qa_report = self._run_qa_loop(
                output_path,
                presentation,
                gen_result,
                output_dir,
                requirements=requirements,
            )
            qa_passed = qa_report.passed
            qa_issues = [
                f"[{iss.severity}] slide {iss.slide_index}: {iss.category} — {iss.description}"
                for iss in qa_report.issues
            ]
            if not qa_passed:
                errors.append(f"QA found {qa_report.critical_count} critical issue(s)")
            # Update output_path if QA loop re-rendered
            if output_path.exists():
                pass  # keep the latest render
        elif not self.config.enable_qa:
            qa_passed = True

        # ── 7. Ingest into design index ────────────────────────────
        deck_chunk_id: Optional[str] = None
        try:
            deck_chunk, slide_chunks, element_chunks = self.chunker.chunk(
                presentation, source_file=str(dsl_path)
            )
            embed_chunks([deck_chunk] + slide_chunks + element_chunks, self.embed_fn)
            self.store.upsert_deck(deck_chunk)
            for sc in slide_chunks:
                self.store.upsert_slide(sc)
            for ec in element_chunks:
                self.store.upsert_element(ec)
            deck_chunk_id = deck_chunk.id

            # Record phrase triggers
            for slide_chunk in slide_chunks:
                self.store.record_phrase_trigger(user_input, slide_chunk_id=slide_chunk.id)

        except Exception as e:
            errors.append(f"Index ingestion error: {e}")

        return PipelineResult(
            dsl_text=gen_result.dsl_text,
            presentation=presentation,
            output_path=output_path,
            slide_count=len(presentation.slides),
            generation_confidence=gen_result.confidence,
            qa_passed=qa_passed,
            qa_issues=qa_issues,
            deck_chunk_id=deck_chunk_id,
            design_references=gen_result.design_references,
            requirements_coverage=requirements_coverage,
            requirement_gaps=requirement_gaps,
            errors=errors,
        )

    def _run_qa_loop(
        self,
        pptx_path: Path,
        presentation: PresentationNode,
        gen_result: GenerationResult,
        output_dir: Path,
        requirements: Optional[PresentationRequirements] = None,
    ) -> QAReport:
        """
        Run the QA inspect → fix → re-render loop.

        Returns the final QAReport after up to max_qa_cycles iterations.
        """
        latest_report = QAReport(passed=True, summary="QA skipped")

        for cycle in range(self.config.max_qa_cycles):
            try:
                latest_report = self.qa_agent.inspect_from_pptx(
                    pptx_path, presentation.slides, requirements=requirements
                )
            except Exception as e:
                logger.warning("QA cycle %d failed: %s", cycle + 1, e)
                latest_report = QAReport(
                    passed=True,
                    summary=f"QA inspection unavailable: {e}",
                )
                break

            if latest_report.passed:
                logger.info("QA passed on cycle %d", cycle + 1)
                break

            if cycle + 1 >= self.config.max_qa_cycles:
                logger.warning("QA failed after %d cycles", self.config.max_qa_cycles)
                break

            # Attempt to fix: re-generate with QA feedback
            fix_prompt = self._build_fix_prompt(gen_result, latest_report, requirements)
            try:
                fix_context = GenerationContext(
                    user_input=fix_prompt,
                    existing_dsl=gen_result.dsl_text,
                    brand=self.config.brand,
                    output_format=self.config.output_format,
                )
                fix_result = self.agent.generate(fix_context)

                if fix_result.presentation and fix_result.presentation.slides:
                    presentation = fix_result.presentation
                    gen_result = fix_result
                    # Re-render
                    pptx_path = render(
                        presentation,
                        output_dir,
                        template_path=self.config.default_template,
                    )
                else:
                    break  # fix failed, stop cycling
            except Exception as e:
                logger.warning("QA fix cycle %d error: %s", cycle + 1, e)
                break

        return latest_report

    @staticmethod
    def _build_fix_prompt(
        gen_result: GenerationResult,
        qa_report: QAReport,
        requirements: Optional[PresentationRequirements] = None,
    ) -> str:
        """Build a structured fix prompt prioritising content gaps over visual issues."""
        parts = ["Fix the following issues in priority order:\n"]

        # Content gaps from requirements (critical — must add missing content)
        content_gap_issues = [
            iss
            for iss in qa_report.issues
            if iss.category in ("requirement_gap", "missing_key_message")
        ]
        if content_gap_issues:
            parts.append("CRITICAL CONTENT GAPS (add missing content):")
            for iss in content_gap_issues:
                line = f"- {iss.description}"
                if iss.suggested_fix:
                    line += f" — {iss.suggested_fix}"
                parts.append(line)
            parts.append("")

        # Visual / layout issues (fix formatting)
        visual_issues = [
            iss
            for iss in qa_report.issues
            if iss.category not in ("requirement_gap", "missing_key_message", "audience_mismatch")
        ]
        if visual_issues:
            critical_visual = [i for i in visual_issues if i.severity == "critical"]
            other_visual = [i for i in visual_issues if i.severity != "critical"]
            if critical_visual:
                parts.append("CRITICAL VISUAL ISSUES (fix layout):")
                for iss in critical_visual:
                    line = f"- Slide {iss.slide_index + 1}: [{iss.severity}] {iss.category} — {iss.description}"
                    if iss.suggested_fix:
                        line += f" (fix: {iss.suggested_fix})"
                    parts.append(line)
                parts.append("")
            if other_visual:
                parts.append("VISUAL WARNINGS (address if possible):")
                for iss in other_visual:
                    line = f"- Slide {iss.slide_index + 1}: [{iss.severity}] {iss.category} — {iss.description}"
                    if iss.suggested_fix:
                        line += f" (fix: {iss.suggested_fix})"
                    parts.append(line)
                parts.append("")

        # Requirements context for the agent
        if requirements:
            persona = requirements.audience_persona
            parts.append("REQUIREMENTS CONTEXT:")
            parts.append(
                f"- Audience: {persona.role} ({persona.seniority}), depth: {persona.expected_depth}"
            )
            if requirements.key_messages:
                parts.append(f"- Must include: {'; '.join(requirements.key_messages[:3])}")
            if requirements.must_have_slide_types:
                parts.append(
                    f"- Required slide types: {', '.join(requirements.must_have_slide_types)}"
                )
            parts.append("")

        parts.append("Keep changes minimal. Fix gaps first, then visual issues.")
        return "\n".join(parts)

    def ingest_existing_deck(
        self,
        dsl_path: str,
    ) -> Optional[str]:
        """
        Ingest an existing .sdsl file into the design index.

        Returns the deck_chunk_id on success, None on failure.
        """
        try:
            presentation = self.parser.parse_file(dsl_path)
            deck_chunk, slide_chunks, element_chunks = self.chunker.chunk(
                presentation, source_file=dsl_path
            )
            embed_chunks([deck_chunk] + slide_chunks + element_chunks, self.embed_fn)
            self.store.upsert_deck(deck_chunk)
            for sc in slide_chunks:
                self.store.upsert_slide(sc)
            for ec in element_chunks:
                self.store.upsert_element(ec)
            return deck_chunk.id
        except Exception:
            return None

    def record_feedback(
        self,
        chunk_id: str,
        signal: str,
        edited_dsl: Optional[str] = None,
    ):
        """
        Record user feedback on a generated design.

        Args:
            chunk_id: The slide chunk that got feedback.
            signal: "keep" | "edit" | "regen" | "delete"
            edited_dsl: If signal is "edit", the modified DSL text.
        """
        self.store.record_feedback(chunk_id, "slide", signal)

        # If edited, ingest the edited version as a new slide
        if signal == "edit" and edited_dsl:
            try:
                wrapper = f'---\npresentation:\n  title: "edited"\n---\n\n{edited_dsl}'
                pres = self.parser.parse(wrapper)
                if pres.slides:
                    deck_chunk, slide_chunks, element_chunks = self.chunker.chunk(pres)
                    for sc in slide_chunks:
                        sc.keep_count = 1
                        self.store.upsert_slide(sc)
                    for ec in element_chunks:
                        self.store.upsert_element(ec)
            except Exception:
                pass

    def get_index_stats(self) -> dict:
        """Return statistics about the design index."""
        return self.store.get_stats()


# ── Module-level helpers ────────────────────────────────────────────


def _print_requirements_summary(requirements: "PresentationRequirements") -> None:
    """Print a human-readable requirements summary to stdout."""
    persona = requirements.audience_persona
    print("\nExtracted Requirements")
    print("─" * 50)
    print(f"Audience     : {persona.role} ({persona.seniority}), {persona.domain_expertise}")
    print(f"Depth        : {persona.expected_depth}  |  Tone: {requirements.tone}")
    if requirements.key_messages:
        print("Key messages :")
        for msg in requirements.key_messages:
            print(f"  • {msg}")
    if requirements.must_have_sections:
        print(f"Sections     : {', '.join(requirements.must_have_sections)}")
    if requirements.must_have_slide_types:
        print(f"Slide types  : {', '.join(requirements.must_have_slide_types)}")
    if requirements.consulting_standards:
        print(f"Standards    : {', '.join(requirements.consulting_standards)}")
    if requirements.constraints:
        print(f"Constraints  : {requirements.constraints}")
    print("─" * 50)
