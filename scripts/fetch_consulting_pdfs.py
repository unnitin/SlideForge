"""
scripts/fetch_consulting_pdfs.py — Crawl consulting firm publications and download PDFs

Targets McKinsey, BCG, and Bain & Company public publications pages.
Downloads reports published 2020+ to data/consulting_pdfs/{firm}/.

Usage:
    python scripts/fetch_consulting_pdfs.py [--firm {mckinsey,bcg,bain,all}]
                                             [--max-per-firm 50]
                                             [--output-dir data/consulting_pdfs]

Respects robots.txt and enforces a 1s delay between requests.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import time
import urllib.parse
import urllib.robotparser
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REQUEST_DELAY = 1.0  # seconds between requests
USER_AGENT = "SlideDSL-Research-Bot/1.0 (academic benchmark; contact: research@example.com)"

# ── Publication index URLs ─────────────────────────────────────────────────────

FIRM_CONFIGS: dict[str, dict] = {
    "mckinsey": {
        "listing_urls": [
            "https://www.mckinsey.com/featured-insights",
            "https://www.mckinsey.com/mgi/research",
        ],
        "pdf_pattern": re.compile(r'href=["\']([^"\']*\.pdf)["\']', re.IGNORECASE),
        "base_url": "https://www.mckinsey.com",
    },
    "bcg": {
        "listing_urls": [
            "https://www.bcg.com/publications",
        ],
        "pdf_pattern": re.compile(r'href=["\']([^"\']*\.pdf)["\']', re.IGNORECASE),
        "base_url": "https://www.bcg.com",
    },
    "bain": {
        "listing_urls": [
            "https://www.bain.com/insights",
        ],
        "pdf_pattern": re.compile(r'href=["\']([^"\']*\.pdf)["\']', re.IGNORECASE),
        "base_url": "https://www.bain.com",
    },
}

# ── Robots.txt helpers ─────────────────────────────────────────────────────────

_rp_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def _get_robots_parser(base_url: str) -> urllib.robotparser.RobotFileParser:
    """Fetch and cache robots.txt for a given base URL."""
    if base_url not in _rp_cache:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = base_url.rstrip("/") + "/robots.txt"
        rp.set_url(robots_url)
        try:
            rp.read()
        except Exception as exc:
            logger.warning("Could not read robots.txt from %s: %s", robots_url, exc)
        _rp_cache[base_url] = rp
    return _rp_cache[base_url]


def _can_fetch(url: str, base_url: str) -> bool:
    """Return True if robots.txt permits fetching this URL."""
    rp = _get_robots_parser(base_url)
    return rp.can_fetch(USER_AGENT, url)


# ── HTTP helpers ───────────────────────────────────────────────────────────────


def _fetch_html(url: str) -> Optional[str]:
    """Fetch URL and return HTML text, or None on failure."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=20) as resp:
            encoding = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(encoding, errors="replace")
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


def _year_from_url(url: str) -> Optional[int]:
    """Heuristically extract the publication year from a URL."""
    m = re.search(r"/(20\d{2})/", url)
    if m:
        return int(m.group(1))
    m = re.search(r"(20\d{2})", url)
    if m:
        return int(m.group(1))
    return None


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve a (possibly relative) href against base_url."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urllib.parse.urljoin(base_url, href)


# ── Core crawl logic ───────────────────────────────────────────────────────────


def _extract_pdf_links(html: str, config: dict, min_year: int = 2020) -> list[str]:
    """Extract PDF links from a listing page, filtered by year >= min_year."""
    pdf_urls: list[str] = []
    for m in config["pdf_pattern"].finditer(html):
        href = m.group(1)
        url = _resolve_url(href, config["base_url"])
        year = _year_from_url(url)
        if year is None or year >= min_year:
            pdf_urls.append(url)
    return list(dict.fromkeys(pdf_urls))  # deduplicate preserving order


def _download_pdf(url: str, dest_dir: Path, firm: str) -> Optional[dict]:
    """Download a PDF to dest_dir. Returns metadata dict or None on failure."""
    filename = Path(urllib.parse.urlsplit(url).path).name
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    dest_path = dest_dir / filename

    if dest_path.exists():
        logger.info("Already downloaded: %s", filename)
        return {
            "url": url,
            "filename": str(dest_path),
            "firm": firm,
            "year": _year_from_url(url),
            "status": "cached",
        }

    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=60) as resp:
            data = resp.read()
        dest_path.write_bytes(data)
        # Estimate page count from PDF header (naive: count "Page" objects)
        page_count = data.count(b"/Page ")
        logger.info("Downloaded %s (%d est. pages)", filename, page_count)
        return {
            "url": url,
            "filename": str(dest_path),
            "firm": firm,
            "year": _year_from_url(url),
            "page_count": page_count,
            "status": "downloaded",
        }
    except Exception as exc:
        logger.warning("Failed to download %s: %s", url, exc)
        return None


def crawl_firm(
    firm: str,
    output_dir: Path,
    max_pdfs: int = 50,
    min_year: int = 2020,
) -> list[dict]:
    """Crawl one firm's publication pages and download PDFs.

    Args:
        firm: One of "mckinsey", "bcg", "bain".
        output_dir: Destination directory for PDFs.
        max_pdfs: Maximum number of PDFs to download.
        min_year: Only download reports from this year onward.

    Returns:
        List of metadata dicts for each downloaded (or cached) PDF.
    """
    config = FIRM_CONFIGS[firm]
    firm_dir = output_dir / firm
    firm_dir.mkdir(parents=True, exist_ok=True)

    all_pdf_urls: list[str] = []

    for listing_url in config["listing_urls"]:
        if not _can_fetch(listing_url, config["base_url"]):
            logger.warning("robots.txt disallows fetching %s — skipping", listing_url)
            continue

        logger.info("[%s] Fetching listing: %s", firm, listing_url)
        html = _fetch_html(listing_url)
        time.sleep(REQUEST_DELAY)

        if html is None:
            continue

        pdf_urls = _extract_pdf_links(html, config, min_year)
        logger.info("[%s] Found %d PDF links on %s", firm, len(pdf_urls), listing_url)
        all_pdf_urls.extend(pdf_urls)

    # Deduplicate
    all_pdf_urls = list(dict.fromkeys(all_pdf_urls))

    downloaded: list[dict] = []
    for url in all_pdf_urls[:max_pdfs]:
        if not _can_fetch(url, config["base_url"]):
            logger.warning("robots.txt disallows fetching %s — skipping", url)
            continue

        result = _download_pdf(url, firm_dir, firm)
        if result:
            downloaded.append(result)
        time.sleep(REQUEST_DELAY)

    return downloaded


def write_manifest(records: list[dict], output_dir: Path) -> Path:
    """Write download manifest CSV to output_dir/manifest.csv."""
    manifest_path = output_dir / "manifest.csv"
    fieldnames = ["firm", "url", "filename", "year", "page_count", "status"]
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    logger.info("Manifest written to %s", manifest_path)
    return manifest_path


# ── CLI ────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Download consulting firm PDFs for QA benchmarking"
    )
    parser.add_argument(
        "--firm",
        choices=["mckinsey", "bcg", "bain", "all"],
        default="all",
        help="Which firm(s) to crawl",
    )
    parser.add_argument(
        "--max-per-firm",
        type=int,
        default=50,
        help="Maximum number of PDFs to download per firm",
    )
    parser.add_argument(
        "--min-year",
        type=int,
        default=2020,
        help="Only download reports published >= this year",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/consulting_pdfs"),
        help="Root directory for downloaded PDFs",
    )
    args = parser.parse_args()

    firms = list(FIRM_CONFIGS.keys()) if args.firm == "all" else [args.firm]
    all_records: list[dict] = []

    for firm in firms:
        logger.info("=== Crawling %s ===", firm.upper())
        records = crawl_firm(
            firm,
            args.output_dir,
            max_pdfs=args.max_per_firm,
            min_year=args.min_year,
        )
        all_records.extend(records)
        logger.info("[%s] Done: %d PDFs", firm, len(records))

    write_manifest(all_records, args.output_dir)
    logger.info("Total: %d PDFs across %d firms", len(all_records), len(firms))


if __name__ == "__main__":
    main()
