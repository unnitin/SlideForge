"""
scripts/benchmark_qa.py — Run QA agent against real consulting slide images

Walks data/consulting_pdfs/ for .jpg images (extracted from PDFs via pdftoppm),
batches them through QAAgent.inspect(), and writes per-slide results to
results/qa_benchmark.json.

Usage:
    python scripts/benchmark_qa.py \\
        --image-dir data/consulting_pdfs \\
        --output results/qa_benchmark.json \\
        --batch-size 6

Pre-requisite: run fetch_consulting_pdfs.py first to download PDFs, then:
    for pdf in data/consulting_pdfs/**/*.pdf; do
        pdftoppm -jpeg -r 150 "$pdf" "${pdf%.pdf}"
    done
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class SlideRecord:
    """Metadata for a single slide image being benchmarked."""

    firm: str
    pdf: str  # relative path to source PDF
    slide_index: int  # 0-based page number within the PDF
    image_path: str  # absolute path to the .jpg file


@dataclass
class BenchmarkResult:
    """Per-slide QA result stored in the output JSON."""

    firm: str
    pdf: str
    slide_index: int
    image_path: str
    passed: bool
    issues: list[dict] = field(default_factory=list)
    error: Optional[str] = None


# ── Image Discovery ───────────────────────────────────────────────────────────


def _parse_image_path(img_path: Path, image_dir: Path) -> Optional[SlideRecord]:
    """Extract firm, PDF name, and slide index from a pdftoppm output path.

    pdftoppm output: {pdf_stem}-{page_number}.jpg  (e.g. report-001.jpg)
    The firm is the subdirectory name under image_dir.
    """
    try:
        rel = img_path.relative_to(image_dir)
        parts = rel.parts
        if len(parts) < 2:
            return None
        firm = parts[0]

        # Extract page number suffix  (last hyphen-delimited token before .jpg)
        stem = img_path.stem  # e.g. "report-001"
        last_dash = stem.rfind("-")
        if last_dash == -1:
            return None
        pdf_stem = stem[:last_dash]
        page_str = stem[last_dash + 1 :]
        if not page_str.isdigit():
            return None
        slide_index = int(page_str) - 1  # convert 1-based to 0-based

        pdf_rel = str(Path(firm) / (pdf_stem + ".pdf"))
        return SlideRecord(
            firm=firm,
            pdf=pdf_rel,
            slide_index=slide_index,
            image_path=str(img_path),
        )
    except Exception:
        return None


def collect_images(image_dir: Path, skip_cover: bool = True) -> list[SlideRecord]:
    """Walk image_dir and collect all .jpg slide images, sorted by firm/pdf/page.

    Args:
        image_dir: Root directory (contains {firm}/ subdirectories).
        skip_cover: If True, skip slide_index 0 of each PDF (cover/TOC heuristic).

    Returns:
        Sorted list of SlideRecord objects.
    """
    records: list[SlideRecord] = []
    for img_path in sorted(image_dir.rglob("*.jpg")):
        rec = _parse_image_path(img_path, image_dir)
        if rec is None:
            continue
        if skip_cover and rec.slide_index == 0:
            logger.debug("Skipping cover slide: %s", img_path.name)
            continue
        records.append(rec)

    logger.info("Collected %d slide images from %s", len(records), image_dir)
    return records


# ── QA Batch Execution ────────────────────────────────────────────────────────


def _build_slide_images(batch: list[SlideRecord]):
    """Convert SlideRecord list to SlideImage list for QAAgent."""
    from agents.qa_agent import SlideImage

    return [
        SlideImage(
            slide_index=rec.slide_index,
            image_path=rec.image_path,
            dsl_text="",  # No DSL available for real consulting slides
        )
        for rec in batch
    ]


def run_benchmark(
    image_dir: Path,
    output_path: Path,
    batch_size: int = 6,
    api_key: Optional[str] = None,
) -> list[BenchmarkResult]:
    """Run QA agent across all collected slide images and write results.

    Args:
        image_dir: Directory containing firm/ subdirectories with .jpg files.
        output_path: Path to write qa_benchmark.json.
        batch_size: Number of slides per QAAgent.inspect() call.
        api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var).

    Returns:
        List of BenchmarkResult objects (one per slide).
    """
    from agents.qa_agent import QAAgent

    output_path.parent.mkdir(parents=True, exist_ok=True)
    qa = QAAgent(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    records = collect_images(image_dir)
    if not records:
        logger.warning("No slide images found in %s", image_dir)
        return []

    results: list[BenchmarkResult] = []
    total = len(records)
    passed_count = 0

    for batch_start in range(0, total, batch_size):
        batch = records[batch_start : batch_start + batch_size]
        logger.info(
            "Processing batch %d–%d / %d …",
            batch_start + 1,
            min(batch_start + batch_size, total),
            total,
        )

        slide_images = _build_slide_images(batch)

        try:
            report = qa.inspect(slide_images, expected_slides=[])
            issues_by_idx: dict[int, list] = {}
            for issue in report.issues:
                issues_by_idx.setdefault(issue.slide_index, []).append(
                    {
                        "severity": issue.severity,
                        "category": issue.category,
                        "description": issue.description,
                        "suggested_fix": issue.suggested_fix,
                    }
                )

            for local_idx, rec in enumerate(batch):
                slide_issues = issues_by_idx.get(local_idx, [])
                has_critical = any(i["severity"] == "critical" for i in slide_issues)
                slide_passed = not has_critical

                if slide_passed:
                    passed_count += 1

                results.append(
                    BenchmarkResult(
                        firm=rec.firm,
                        pdf=rec.pdf,
                        slide_index=rec.slide_index,
                        image_path=rec.image_path,
                        passed=slide_passed,
                        issues=slide_issues,
                    )
                )

        except Exception as exc:
            logger.error("Batch %d failed: %s", batch_start, exc)
            for rec in batch:
                results.append(
                    BenchmarkResult(
                        firm=rec.firm,
                        pdf=rec.pdf,
                        slide_index=rec.slide_index,
                        image_path=rec.image_path,
                        passed=False,
                        error=str(exc),
                    )
                )

        processed = min(batch_start + batch_size, total)
        logger.info(
            "Progress: %d / %d slides | pass rate so far: %.1f%%",
            processed,
            total,
            100 * passed_count / processed,
        )

    # Write output
    output_path.write_text(
        json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Results written to %s", output_path)
    logger.info(
        "Final: %d slides | %d passed (%.1f%%)",
        total,
        passed_count,
        100 * passed_count / total if total else 0,
    )

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark QA agent against real consulting slide images"
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=Path("data/consulting_pdfs"),
        help="Directory containing firm/ subdirs with .jpg slide images",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/qa_benchmark.json"),
        help="Output JSON file path",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=6,
        help="Number of slides to send per QAAgent.inspect() call",
    )
    parser.add_argument(
        "--no-skip-cover",
        action="store_true",
        help="Do not skip first page of each PDF",
    )
    args = parser.parse_args()

    run_benchmark(
        image_dir=args.image_dir,
        output_path=args.output,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
