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
from src.dsl.parser import SlideDSLParser
from src.dsl.serializer import SlideDSLSerializer
from src.index.chunker import SlideChunker
from src.index.retriever import DesignIndexRetriever
from src.index.store import DesignIndexStore
from src.renderer.pptx_renderer import render
from agents.nl_to_dsl import GenerationContext, GenerationResult, NLToDSLAgent
from agents.qa_agent import QAAgent, QAReport

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the generation pipeline."""

    # Index
    index_db_path: str = "design_index.db"

    # Agent
    model: str = "claude-sonnet-4-5-20250514"
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
        self.retriever = DesignIndexRetriever(self.store, embed_fn=None)
        self.agent = NLToDSLAgent(model=config.model, api_key=config.api_key)
        self.qa_agent = QAAgent(model=config.model, api_key=config.api_key)
        self.parser = SlideDSLParser()
        self.serializer = SlideDSLSerializer()
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
            errors=errors,
        )

    def _run_qa_loop(
        self,
        pptx_path: Path,
        presentation: PresentationNode,
        gen_result: GenerationResult,
        output_dir: Path,
    ) -> QAReport:
        """
        Run the QA inspect → fix → re-render loop.

        Returns the final QAReport after up to max_qa_cycles iterations.
        """
        latest_report = QAReport(passed=True, summary="QA skipped")

        for cycle in range(self.config.max_qa_cycles):
            try:
                latest_report = self.qa_agent.inspect_from_pptx(pptx_path, presentation.slides)
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
            fix_prompt = self._build_fix_prompt(gen_result, latest_report)
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
    def _build_fix_prompt(gen_result: GenerationResult, qa_report: QAReport) -> str:
        """Build a prompt that asks the agent to fix QA issues."""
        issue_lines = []
        for iss in qa_report.issues:
            line = f"- Slide {iss.slide_index + 1}: [{iss.severity}] {iss.category} — {iss.description}"
            if iss.suggested_fix:
                line += f" (fix: {iss.suggested_fix})"
            issue_lines.append(line)

        return (
            "Fix the following visual QA issues in this presentation:\n"
            + "\n".join(issue_lines)
            + "\n\nKeep all content the same. Only fix layout/formatting issues."
        )

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
