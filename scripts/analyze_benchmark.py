"""
scripts/analyze_benchmark.py — Parse qa_benchmark.json and print calibration report

Reads results/qa_benchmark.json (produced by benchmark_qa.py) and prints:
  - Per-firm pass rates, critical issues per slide, top issue categories
  - Overall totals
  - Calibration assessment: which checks may be over/under-sensitive

Usage:
    python scripts/analyze_benchmark.py \\
        --input results/qa_benchmark.json \\
        --output results/qa_benchmark_summary.md
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


# ── Data Loading ──────────────────────────────────────────────────────────────


def load_results(input_path: Path) -> list[dict]:
    """Load benchmark results from JSON file."""
    if not input_path.exists():
        raise FileNotFoundError(f"Results file not found: {input_path}")
    with input_path.open(encoding="utf-8") as f:
        return json.load(f)


# ── Analysis ──────────────────────────────────────────────────────────────────


def analyze(results: list[dict]) -> dict:
    """Compute per-firm and overall statistics from benchmark results.

    Returns a dict with keys: firms, totals, issue_counts, category_by_firm.
    """
    firms: dict[str, dict] = defaultdict(lambda: {"slides": 0, "passed": 0, "criticals": 0})
    issue_counts: Counter = Counter()
    category_by_firm: dict[str, Counter] = defaultdict(Counter)
    severity_counts: Counter = Counter()

    for rec in results:
        firm = rec.get("firm", "unknown")
        firms[firm]["slides"] += 1
        if rec.get("passed"):
            firms[firm]["passed"] += 1

        for issue in rec.get("issues", []):
            cat = issue.get("category", "unknown")
            sev = issue.get("severity", "unknown")
            issue_counts[cat] += 1
            category_by_firm[firm][cat] += 1
            severity_counts[sev] += 1
            if sev == "critical":
                firms[firm]["criticals"] += 1

    total_slides = len(results)
    total_passed = sum(r.get("passed", False) for r in results)
    total_criticals = sum(
        1 for r in results for i in r.get("issues", []) if i.get("severity") == "critical"
    )

    return {
        "firms": dict(firms),
        "totals": {
            "slides": total_slides,
            "passed": total_passed,
            "criticals": total_criticals,
        },
        "issue_counts": issue_counts,
        "category_by_firm": {k: dict(v) for k, v in category_by_firm.items()},
        "severity_counts": dict(severity_counts),
    }


# ── Calibration Heuristics ────────────────────────────────────────────────────

# Thresholds for automated calibration commentary
OVER_SENSITIVE_THRESHOLD = 0.50  # flagged on >50% of slides → likely over-sensitive
BLIND_SPOT_THRESHOLD = 0.02  # flagged on <2% of slides → possible blind spot

KNOWN_ISSUE_DESCRIPTIONS = {
    "action_title": (
        "Real consulting slides frequently use section-header-style titles "
        "(e.g. 'Market Opportunity') rather than action sentences. "
        "Consider relaxing this check for section_divider slide types."
    ),
    "source_line": (
        "Many consulting slides lack visible source attribution in the body. "
        "Rate around 40–50% is plausible and likely reflects genuine gaps, "
        "not over-sensitivity."
    ),
    "overlap": (
        "High overlap rate may indicate our renderer geometry differs from "
        "consulting templates. Investigate slide margin and column width settings."
    ),
    "contrast": (
        "Check may be flagging intentional dark-on-dark brand color usage "
        "common in McKinsey/BCG dark-background slides."
    ),
    "alignment": (
        "Alignment check on external slides may be miscalibrated to our "
        "internal grid; consulting firms use different baseline grids."
    ),
}


def _calibration_note(category: str, rate: float, total: int) -> str:
    """Generate a calibration note for a given issue category and rate."""
    if rate > OVER_SENSITIVE_THRESHOLD:
        direction = "OVER-SENSITIVE"
        generic = (
            f"Flagged on {rate:.0%} of slides — this high rate suggests the check "
            "may be too strict for real consulting slide conventions."
        )
    elif rate < BLIND_SPOT_THRESHOLD:
        direction = "POTENTIAL BLIND SPOT"
        generic = (
            f"Only flagged on {rate:.0%} of slides — may indicate the check is "
            "rarely triggered, possibly missing real issues."
        )
    else:
        return ""  # no note needed for well-calibrated checks

    specific = KNOWN_ISSUE_DESCRIPTIONS.get(category, "")
    note = f"  {direction}: {generic}"
    if specific:
        note += f"\n  → {specific}"
    return note


# ── Report Generation ─────────────────────────────────────────────────────────


def _top_n(counter: dict | Counter, n: int = 10) -> list[tuple[str, int]]:
    """Return top-n (category, count) sorted by count descending."""
    return sorted(counter.items(), key=lambda x: x[1], reverse=True)[:n]


def build_report(stats: dict) -> str:
    """Build a human-readable benchmark report string."""
    totals = stats["totals"]
    firms = stats["firms"]
    issue_counts = stats["issue_counts"]
    category_by_firm = stats["category_by_firm"]
    total_slides = totals["slides"]

    lines: list[str] = []

    # ── Header ──
    lines.append(f"=== QA BENCHMARK: {total_slides:,} CONSULTING SLIDES (2020–2025) ===\n")

    # ── Per-firm table ──
    lines.append(
        f"{'Firm':<12} | {'Slides':>6} | {'Pass%':>6} | {'Crit/slide':>10} | Top Issue Category"
    )
    lines.append("-" * 75)

    for firm in sorted(firms.keys()):
        fd = firms[firm]
        n = fd["slides"]
        if n == 0:
            continue
        pass_pct = 100 * fd["passed"] / n
        crit_rate = fd["criticals"] / n
        top_cat = "—"
        if firm in category_by_firm and category_by_firm[firm]:
            top_cat_name, top_cat_count = _top_n(category_by_firm[firm], 1)[0]
            top_cat = f"{top_cat_name} ({100 * top_cat_count / n:.0f}%)"
        lines.append(
            f"{firm.capitalize():<12} | {n:>6,} | {pass_pct:>5.1f}% | {crit_rate:>10.1f} | {top_cat}"
        )

    # Total row
    if total_slides > 0:
        pass_pct = 100 * totals["passed"] / total_slides
        crit_rate = totals["criticals"] / total_slides
        lines.append("-" * 75)
        lines.append(
            f"{'TOTAL':<12} | {total_slides:>6,} | {pass_pct:>5.1f}% | {crit_rate:>10.1f} |"
        )

    lines.append("")

    # ── Most common critical issues ──
    lines.append("Most common issue categories (across all firms):")
    for rank, (cat, count) in enumerate(_top_n(issue_counts, 10), start=1):
        pct = 100 * count / total_slides if total_slides else 0
        lines.append(f"  {rank:>2}. {cat:<25}: {count:>5,} slides ({pct:.0f}%)")

    lines.append("")

    # ── Calibration assessment ──
    lines.append("CALIBRATION ASSESSMENT:")
    has_notes = False
    for cat, count in _top_n(issue_counts, 20):
        rate = count / total_slides if total_slides else 0
        note = _calibration_note(cat, rate, total_slides)
        if note:
            has_notes = True
            lines.append(f"\n- {cat}:")
            lines.append(note)

    if not has_notes:
        lines.append("  All issue categories appear within expected ranges (2%–50% of slides).")

    lines.append("")

    # ── Coverage summary ──
    lines.append("COVERAGE NOTES:")
    if total_slides == 0:
        lines.append("  No slides processed. Run fetch_consulting_pdfs.py and pdftoppm first.")
    elif total_slides < 100:
        lines.append(
            f"  WARNING: Only {total_slides} slides processed. "
            "Benchmark validity requires ≥100 slides; target is 1,000+."
        )
    else:
        lines.append(f"  Benchmark covers {total_slides:,} slides across {len(firms)} firms.")

    return "\n".join(lines)


def build_markdown_report(stats: dict, raw_results: list[dict]) -> str:
    """Build a Markdown version of the report for saving to .md."""
    plain = build_report(stats)
    # Wrap in a code block for easy reading, with a preamble
    md_lines = [
        "# QA Benchmark Report",
        "",
        f"Generated from {len(raw_results):,} slide records.",
        "",
        "```",
        plain,
        "```",
        "",
        "## Raw Statistics",
        "",
        "```json",
        json.dumps(stats["totals"], indent=2),
        "```",
    ]
    return "\n".join(md_lines)


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Analyze QA benchmark results and print calibration report"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("results/qa_benchmark.json"),
        help="Path to qa_benchmark.json produced by benchmark_qa.py",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/qa_benchmark_summary.md"),
        help="Path to write Markdown summary report",
    )
    args = parser.parse_args()

    results = load_results(args.input)
    stats = analyze(results)

    plain_report = build_report(stats)
    print(plain_report)

    md_report = build_markdown_report(stats, results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md_report, encoding="utf-8")
    print(f"\nMarkdown summary written to {args.output}")


if __name__ == "__main__":
    main()
