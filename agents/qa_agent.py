"""
agents/qa_agent.py — Visual QA Inspection Agent

Inspects rendered slides for visual and content issues using Claude's
vision capabilities. Runs after every render cycle as part of the
orchestrator pipeline.

Flow:
    .pptx → convert to images (LibreOffice + pdftoppm) → send to Claude
    → parse structured QA issues → return QAReport

See specs/AGENT_SPEC.md for full contract.
See agents/prompts/qa_inspection.txt for system prompt.
"""

from __future__ import annotations

import base64
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anthropic

from src.dsl.models import SlideNode
from src.dsl.serializer import SlideForgeSerializer

logger = logging.getLogger(__name__)


@dataclass
class SlideImage:
    """A rendered slide image paired with its DSL source."""

    slide_index: int
    image_path: str
    dsl_text: str


@dataclass
class QAIssue:
    """A single QA issue found during inspection."""

    slide_index: int
    severity: str  # "critical", "warning", "minor"
    category: str  # "overlap", "overflow", "alignment", "contrast", "spacing", "content_missing"
    description: str
    suggested_fix: Optional[str] = None


@dataclass
class QAReport:
    """Result of QA inspection across all slides."""

    issues: list[QAIssue] = field(default_factory=list)
    passed: bool = False
    summary: str = ""

    @property
    def critical_count(self) -> int:
        """Number of critical issues."""
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        """Number of warning issues."""
        return sum(1 for i in self.issues if i.severity == "warning")


class QAAgent:
    """
    Visual QA inspection agent — inspects rendered slides for issues.

    Uses Claude Sonnet with vision to analyze rendered slide images
    against the DSL specification that produced them.
    """

    MAX_QA_CYCLES = 3

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250514",
        api_key: Optional[str] = None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.serializer = SlideForgeSerializer()
        self._system_prompt = self._load_system_prompt()

    def inspect(
        self,
        slide_images: list[SlideImage],
        expected_slides: list[SlideNode],
    ) -> QAReport:
        """
        Inspect rendered slide images for visual and content issues.

        Args:
            slide_images: Rendered slide images with their DSL source.
            expected_slides: The SlideNode objects that were rendered.

        Returns:
            QAReport with any issues found and pass/fail status.
        """
        if not slide_images:
            return QAReport(passed=True, summary="No slides to inspect.")

        content = self._build_message_content(slide_images)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self._system_prompt,
            messages=[{"role": "user", "content": content}],
        )

        raw_text = response.content[0].text
        report = self._parse_response(raw_text)
        return report

    def inspect_from_pptx(
        self,
        pptx_path: str | Path,
        expected_slides: list[SlideNode],
    ) -> QAReport:
        """
        Convenience method: convert .pptx to images, then inspect.

        Args:
            pptx_path: Path to the rendered .pptx file.
            expected_slides: SlideNode list that produced the .pptx.

        Returns:
            QAReport with any issues found.
        """
        image_paths = pptx_to_images(Path(pptx_path))

        slide_images: list[SlideImage] = []
        for i, img_path in enumerate(image_paths):
            dsl_text = ""
            if i < len(expected_slides):
                dsl_text = self.serializer.serialize_slide(expected_slides[i])
            slide_images.append(
                SlideImage(
                    slide_index=i,
                    image_path=str(img_path),
                    dsl_text=dsl_text,
                )
            )

        return self.inspect(slide_images, expected_slides)

    # ── Prompt Building ────────────────────────────────────────────

    def _load_system_prompt(self) -> str:
        """Load the QA inspection system prompt."""
        prompt_path = Path(__file__).parent / "prompts" / "qa_inspection.txt"
        return prompt_path.read_text(encoding="utf-8")

    def _build_message_content(self, slide_images: list[SlideImage]) -> list[dict]:
        """Build multi-modal message content with images and DSL context."""
        content: list[dict] = []

        content.append(
            {
                "type": "text",
                "text": (
                    f"Inspect these {len(slide_images)} rendered slides. "
                    "For each slide I'll provide the rendered image and the DSL "
                    "specification that produced it."
                ),
            }
        )

        for si in slide_images:
            # Add slide header
            content.append(
                {
                    "type": "text",
                    "text": f"\n--- Slide {si.slide_index + 1} ---\nDSL:\n```\n{si.dsl_text}\n```",
                }
            )

            # Add image (base64 encoded)
            img_path = Path(si.image_path)
            if img_path.exists():
                img_data = base64.standard_b64encode(img_path.read_bytes()).decode("utf-8")
                media_type = "image/jpeg"
                if img_path.suffix.lower() == ".png":
                    media_type = "image/png"

                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_data,
                        },
                    }
                )
            else:
                content.append(
                    {
                        "type": "text",
                        "text": f"[Image not found: {si.image_path}]",
                    }
                )

        content.append(
            {
                "type": "text",
                "text": "\nNow provide your QA report following the output format.",
            }
        )

        return content

    # ── Response Parsing ───────────────────────────────────────────

    def _parse_response(self, text: str) -> QAReport:
        """Parse the QA agent's structured text response into a QAReport."""
        issues: list[QAIssue] = []
        current_slide_idx = -1

        # Pattern: SLIDE {n}: ...
        slide_pattern = re.compile(r"SLIDE\s+(\d+):", re.IGNORECASE)
        # Pattern: - [CRITICAL/WARNING/MINOR] category: description
        issue_pattern = re.compile(
            r"-\s*\[(CRITICAL|WARNING|MINOR)\]\s*(\w+):\s*(.+)",
            re.IGNORECASE,
        )
        # Pattern: Suggested fix: ...
        fix_pattern = re.compile(r"Suggested fix:\s*(.+)", re.IGNORECASE)

        lines = text.strip().split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Check for slide header
            slide_match = slide_pattern.match(line)
            if slide_match:
                current_slide_idx = int(slide_match.group(1)) - 1  # 0-indexed

            # Check for issue
            issue_match = issue_pattern.match(line)
            if issue_match and current_slide_idx >= 0:
                severity = issue_match.group(1).lower()
                category = issue_match.group(2).lower()
                description = issue_match.group(3).strip()

                # Check next line for suggested fix
                suggested_fix = None
                if i + 1 < len(lines):
                    fix_match = fix_pattern.match(lines[i + 1].strip())
                    if fix_match:
                        suggested_fix = fix_match.group(1).strip()
                        i += 1

                issues.append(
                    QAIssue(
                        slide_index=current_slide_idx,
                        severity=severity,
                        category=category,
                        description=description,
                        suggested_fix=suggested_fix,
                    )
                )

            i += 1

        # Determine pass/fail
        critical_count = sum(1 for iss in issues if iss.severity == "critical")
        passed = critical_count == 0

        # Extract summary from PASS/FAIL line
        summary = "PASS" if passed else f"FAIL: {critical_count} critical issue(s)"
        for line in lines:
            if line.strip().upper().startswith(("PASS:", "FAIL:")):
                summary = line.strip()
                break

        return QAReport(issues=issues, passed=passed, summary=summary)


# ── Image Conversion Utilities ─────────────────────────────────────


def pptx_to_images(
    pptx_path: Path,
    output_dir: Optional[Path] = None,
    dpi: int = 150,
) -> list[Path]:
    """
    Convert a .pptx file to a list of slide images.

    Uses LibreOffice to convert to PDF, then pdftoppm for rasterization.
    Falls back to LibreOffice-only PNG export if pdftoppm is unavailable.

    Args:
        pptx_path: Path to the .pptx file.
        output_dir: Where to write images. Defaults to a temp directory.
        dpi: Resolution for rasterization.

    Returns:
        List of paths to generated slide images, sorted by slide index.
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="slideforge_qa_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Convert .pptx → .pdf via LibreOffice
    pdf_path = _pptx_to_pdf(pptx_path, output_dir)

    if pdf_path is None:
        logger.warning("LibreOffice conversion failed; returning empty image list")
        return []

    # Step 2: Convert .pdf → images via pdftoppm
    image_paths = _pdf_to_images(pdf_path, output_dir, dpi)

    if not image_paths:
        logger.warning("pdftoppm conversion failed; returning empty image list")

    return sorted(image_paths)


def _pptx_to_pdf(pptx_path: Path, output_dir: Path) -> Optional[Path]:
    """Convert .pptx to .pdf using LibreOffice headless."""
    try:
        subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(pptx_path),
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        pdf_name = pptx_path.stem + ".pdf"
        pdf_path = output_dir / pdf_name
        if pdf_path.exists():
            return pdf_path
        return None
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("LibreOffice conversion failed: %s", e)
        return None


def _pdf_to_images(
    pdf_path: Path,
    output_dir: Path,
    dpi: int = 150,
) -> list[Path]:
    """Convert PDF pages to JPEG images using pdftoppm."""
    try:
        prefix = str(output_dir / "slide")
        subprocess.run(
            [
                "pdftoppm",
                "-jpeg",
                "-r",
                str(dpi),
                str(pdf_path),
                prefix,
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        # pdftoppm outputs: slide-1.jpg, slide-2.jpg, ...
        return sorted(output_dir.glob("slide-*.jpg"))
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("pdftoppm conversion failed: %s", e)
        return []
