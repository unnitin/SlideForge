"""
Stratified-sample QA benchmark: picks evenly-spaced slides from each PDF
(skipping cover), sends them to QAAgent in batches of 8, writes JSON.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/sample_benchmark.py \
        --image-dir data/consulting_pdfs \
        --output results/qa_benchmark.json \
        --slides-per-pdf 6
"""

from __future__ import annotations
import argparse
import json
import logging
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    firm: str
    pdf: str
    slide_index: int
    image_path: str
    passed: bool
    issues: list[dict] = field(default_factory=list)
    error: Optional[str] = None


def collect_sample(image_dir: Path, slides_per_pdf: int) -> list[tuple[str, str, int, Path]]:
    """Return (firm, pdf_stem, slide_idx, image_path) tuples, stratified by PDF."""
    # Group images by PDF
    groups: dict[tuple, list] = defaultdict(list)
    for img in sorted(image_dir.rglob("*.jpg")):
        rel = img.relative_to(image_dir)
        parts = rel.parts
        if len(parts) < 2:
            continue
        firm = parts[0]
        stem = img.stem
        last_dash = stem.rfind("-")
        if last_dash == -1:
            continue
        pdf_stem = stem[:last_dash]
        page_str = stem[last_dash + 1 :]
        if not page_str.isdigit():
            continue
        slide_idx = int(page_str) - 1  # 0-based
        if slide_idx == 0:  # skip cover
            continue
        groups[(firm, pdf_stem)].append((slide_idx, img))

    sample = []
    for (firm, pdf_stem), pages in sorted(groups.items()):
        pages.sort()
        if len(pages) <= slides_per_pdf:
            chosen = pages
        else:
            step = len(pages) / slides_per_pdf
            chosen = [pages[int(i * step)] for i in range(slides_per_pdf)]
        for slide_idx, img in chosen:
            sample.append((firm, pdf_stem + ".pdf", slide_idx, img))

    log.info("Sample: %d slides from %d PDFs", len(sample), len(groups))
    return sample


def run(image_dir: Path, output: Path, slides_per_pdf: int, batch_size: int):
    from agents.qa_agent import QAAgent, SlideImage

    qa = QAAgent(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    sample = collect_sample(image_dir, slides_per_pdf)
    results: list[BenchmarkResult] = []
    passed = 0

    for start in range(0, len(sample), batch_size):
        batch = sample[start : start + batch_size]
        slide_images = [
            SlideImage(slide_index=i, image_path=str(img), dsl_text="")
            for i, (firm, pdf, sidx, img) in enumerate(batch)
        ]
        log.info("Batch %d–%d / %d …", start + 1, start + len(batch), len(sample))
        try:
            report = qa.inspect(slide_images, [])
            issues_by_idx: dict[int, list] = defaultdict(list)
            for iss in report.issues:
                issues_by_idx[iss.slide_index].append(
                    {
                        "severity": iss.severity,
                        "category": iss.category,
                        "description": iss.description,
                        "suggested_fix": iss.suggested_fix,
                    }
                )
            for li, (firm, pdf, sidx, img) in enumerate(batch):
                iss = issues_by_idx.get(li, [])
                ok = not any(i["severity"] == "critical" for i in iss)
                if ok:
                    passed += 1
                results.append(
                    BenchmarkResult(
                        firm=firm,
                        pdf=pdf,
                        slide_index=sidx,
                        image_path=str(img),
                        passed=ok,
                        issues=iss,
                    )
                )
        except Exception as e:
            log.error("Batch failed: %s", e)
            for firm, pdf, sidx, img in batch:
                results.append(
                    BenchmarkResult(
                        firm=firm,
                        pdf=pdf,
                        slide_index=sidx,
                        image_path=str(img),
                        passed=False,
                        error=str(e),
                    )
                )
        done = min(start + batch_size, len(sample))
        log.info("Progress %d/%d  pass=%.0f%%", done, len(sample), 100 * passed / done)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    log.info(
        "Wrote %d results to %s  (pass=%.1f%%)",
        len(results),
        output,
        100 * passed / len(results) if results else 0,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image-dir", type=Path, default=Path("data/consulting_pdfs"))
    p.add_argument("--output", type=Path, default=Path("results/qa_benchmark.json"))
    p.add_argument("--slides-per-pdf", type=int, default=6)
    p.add_argument("--batch-size", type=int, default=8)
    a = p.parse_args()
    run(a.image_dir, a.output, a.slides_per_pdf, a.batch_size)


if __name__ == "__main__":
    main()
