#!/usr/bin/env python3
"""
scripts/run_pipeline.py — End-to-end pipeline test CLI.

Runs the full SlideForge pipeline: NL → retrieve → generate DSL → render → QA → .pptx

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/run_pipeline.py "Create a 6-slide AI strategy deck for the board"

Options:
    --output-dir DIR       Where to write output files (default: ./output)
    --index-db PATH        Path to design index SQLite DB (default: design_index.db)
    --audience AUDIENCE    Target audience (default: "executive")
    --slides N             Approximate slide count
    --no-qa                Skip the QA loop
    --embed-backend BACKEND  "auto" | "sentence_transformers" | "hash" (default: auto)
    --verbose              Show debug logging
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    ap = argparse.ArgumentParser(
        description="Run the full SlideForge generation pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("prompt", help="Natural language description of the desired presentation")
    ap.add_argument("--output-dir", default="./output", help="Output directory (default: ./output)")
    ap.add_argument("--index-db", default="design_index.db", help="Design index DB path")
    ap.add_argument("--audience", default="executive", help="Target audience (default: executive)")
    ap.add_argument("--slides", type=int, default=None, help="Target slide count")
    ap.add_argument("--no-qa", action="store_true", help="Disable QA loop")
    ap.add_argument(
        "--embed-backend",
        default="auto",
        choices=["auto", "sentence_transformers", "hash"],
        help="Embedding backend (default: auto)",
    )
    ap.add_argument("--verbose", action="store_true", help="Enable debug logging")
    ap.add_argument(
        "--interactive",
        action="store_true",
        help="Show extracted requirements and confirm before generating",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        print("       export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    from src.services.orchestrator import Orchestrator, PipelineConfig

    config = PipelineConfig(
        index_db_path=args.index_db,
        api_key=api_key,
        output_dir=args.output_dir,
        enable_qa=not args.no_qa,
        embedding_backend=args.embed_backend,
        interactive=args.interactive,
    )

    print("\nSlideForge Pipeline")
    print(f"{'─' * 50}")
    print(f"Prompt   : {args.prompt}")
    print(f"Audience : {args.audience}")
    print(f"Output   : {args.output_dir}")
    print(f"Index DB : {args.index_db}")
    print(f"QA       : {'disabled' if args.no_qa else 'enabled'}")
    print(f"Embeddings: {args.embed_backend}")
    print(f"Interactive: {args.interactive}")
    print(f"{'─' * 50}\n")

    print("Initializing orchestrator...")
    orch = Orchestrator(config)

    index_stats = orch.get_index_stats()
    print(
        f"Index: {index_stats.get('slide_chunks', 0)} slides, "
        f"{index_stats.get('deck_chunks', 0)} decks in design index\n"
    )

    print("Running pipeline...")
    result = orch.generate(
        user_input=args.prompt,
        audience=args.audience,
        target_slides=args.slides,
    )

    print(f"\n{'─' * 50}")
    print("Pipeline complete")
    print(f"{'─' * 50}")
    print(f"Slides generated : {result.slide_count}")
    print(f"Confidence       : {result.generation_confidence:.2f}")
    print(f"QA passed        : {result.qa_passed}")
    print(f"Req. coverage    : {result.requirements_coverage:.0%}")

    if result.requirement_gaps:
        print(f"Requirement gaps ({len(result.requirement_gaps)}):")
        for gap in result.requirement_gaps[:5]:
            print(f"  ! {gap}")
        if len(result.requirement_gaps) > 5:
            print(f"  ... and {len(result.requirement_gaps) - 5} more")

    if result.qa_issues:
        print(f"QA issues ({len(result.qa_issues)}):")
        for issue in result.qa_issues[:5]:  # show first 5
            print(f"  • {issue}")
        if len(result.qa_issues) > 5:
            print(f"  ... and {len(result.qa_issues) - 5} more")

    if result.design_references:
        print(f"Design refs used : {', '.join(result.design_references[:3])}")

    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  ✗ {err}")

    if result.output_path:
        print(f"\nOutput: {result.output_path}")
    else:
        print("\nNo .pptx generated (check errors above).")
        if result.dsl_text:
            dsl_preview = result.dsl_text[:500]
            print(f"\nDSL preview:\n{dsl_preview}...")
        sys.exit(1)


if __name__ == "__main__":
    main()
