"""One-shot script to download known-public consulting PDFs."""

import time
import urllib.request
import pathlib
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; research-bot/1.0)"

PDFS = [
    {
        "firm": "mckinsey",
        "url": "https://www.mckinsey.com/~/media/mckinsey/industries/energy%20and%20materials/our%20insights/global%20energy%20perspective%202025/global-energy-perspective-2025.pdf",
        "title": "Global Energy Perspective 2025",
    },
    {
        "firm": "mckinsey",
        "url": "https://www.mckinsey.com/~/media/mckinsey/featured%20insights/future%20of%20organizations/the%20future%20of%20work%20after%20covid%2019/the-future-of-work-after-covid-19-report-vf.pdf",
        "title": "Future of Work After COVID-19",
    },
    {
        "firm": "mckinsey",
        "url": "https://www.mckinsey.com/~/media/mckinsey/industries/financial%20services/our%20insights/accelerating%20winds%20of%20change%20in%20global%20payments/2020-mckinsey-global-payments-report-vf.pdf",
        "title": "2020 Global Payments Report",
    },
    {
        "firm": "mckinsey",
        "url": "https://www.mckinsey.com/~/media/mckinsey/business%20functions/mckinsey%20digital/our%20insights/the%20top%20trends%20in%20tech%202024/mckinsey-technology-trends-outlook-2024.pdf",
        "title": "Technology Trends Outlook 2024",
    },
    {
        "firm": "mckinsey",
        "url": "https://www.mckinsey.com/~/media/mckinsey/business%20functions/mckinsey%20digital/our%20insights/mckinsey%20technology%20trends%20outlook%202023/mckinsey-technology-trends-outlook-2023-v5.pdf",
        "title": "Technology Trends Outlook 2023",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/BCG-COVID-19-BCG-Perspectives-Version2.pdf",
        "title": "BCG COVID-19 Perspectives",
    },
    {
        "firm": "bcg",
        "url": "https://web-assets.bcg.com/40/84/80b567044409b74c32806275a3c1/bcg-2020-annual-sustainability-report-apr-2021-r2.pdf",
        "title": "2020 Annual Sustainability Report",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/BCG-Executive-Perspectives-BCGs-Guide-to-Cost-and-Growth-2025-15Jan2025.pdf",
        "title": "Guide to Cost and Growth 2025",
    },
    {
        "firm": "bcg",
        "url": "https://web-assets.bcg.com/img-src/BCG-Transforming-Telcos-with-Artificial-Intelligence-Jun-2020_tcm9-252103.pdf",
        "title": "Transforming Telcos with AI",
    },
    {
        "firm": "bcg",
        "url": "https://web-assets.bcg.com/fb/64/e10897864913a480415d0e1fe3c6/bcg-global-wealth-report-2023-june-2023.pdf",
        "title": "Global Wealth Report 2023",
    },
    {
        "firm": "bcg",
        "url": "https://web-assets.bcg.com/78/f0/82b96e174fffb219f9f73177a3f0/2024-gam-report-may-2024.pdf",
        "title": "Global Asset Management 2024",
    },
    {
        "firm": "bcg",
        "url": "https://web-assets.bcg.com/0b/f6/c2880f9f4472955538567a5bcb6a/ai-radar-2025-slideshow-jan-2025-r.pdf",
        "title": "AI RADAR 2025",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/BCG-Wheres-the-Value-in-AI.pdf",
        "title": "Where's the Value in AI",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/BCG-Executive-Perspectives-CEOs-Guide-to-Maximizing-Value-from-AI-EP0-3July2024.pdf",
        "title": "CEO Guide to AI Value",
    },
    {
        "firm": "bcg",
        "url": "https://web-assets.bcg.com/c1/a7/af0e57dc4b47a31eb7409d981d3e/mitsmr-bcg-ai-report-november-2024.pdf",
        "title": "Learning to Manage Uncertainty With AI",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/BCG-Executive-Perspectives-2022-Future-of-Marketing-and-Sales.pdf",
        "title": "Future of Marketing and Sales",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/BCG-Executive-Perspectives-Future-of-Procurement-with-AI-2025-27Feb2025.pdf",
        "title": "Future of Procurement with AI",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/Capital-Markets-Investment-Banking-Update-2024-2025.pdf",
        "title": "Capital Markets Update 2024-2025",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/The-Widening-AI-Value-Gap-Sept-2025.pdf",
        "title": "The Widening AI Value Gap",
    },
    {
        "firm": "bcg",
        "url": "https://media-publications.bcg.com/2024-Annual-Sustainability-Report-May-2025.pdf",
        "title": "BCG 2024 Sustainability Report",
    },
    {
        "firm": "bain",
        "url": "https://www.bain.com/contentassets/d620202718c146359acb05c02d9060db/bain-report_the-working-future.pdf",
        "title": "The Working Future",
    },
    {
        "firm": "bain",
        "url": "https://www.bain.com/contentassets/ef45097bcaf54c0b9eb46f6fbd2d0e39/bain_report_winning_with_the_indian_consumer.pdf",
        "title": "Winning with the Indian Consumer",
    },
    {
        "firm": "bain",
        "url": "https://www.bain.com/contentassets/f8361c5cd99e4f40bbbf83c17d6a91b9/bain_brief-management_tools_and_trends.pdf",
        "title": "Management Tools and Trends",
    },
    {
        "firm": "bain",
        "url": "https://www.bain.com/contentassets/b9b584a6ffa942e093cba1eaf2b2d916/changing_gears_2020_download.pdf",
        "title": "Changing Gears 2020",
    },
    {
        "firm": "bain",
        "url": "https://www.bain.com/globalassets/noindex/2024/bain_report_global-private-equity-report-2024.pdf",
        "title": "Global Private Equity Report 2024",
    },
    {
        "firm": "bain",
        "url": "https://psik.org.pl/images/Dane-i-raporty/Publikacje-czlonkow/Global_Private_Equity_Report_2025___Bain__Company.pdf",
        "title": "Global Private Equity Report 2025",
    },
    {
        "firm": "bain",
        "url": "https://media.bain.com/Images/BAIN_REPORT_Spatial_economics.pdf",
        "title": "Spatial Economics",
    },
    {
        "firm": "bain",
        "url": "https://www.hkdca.com/wp-content/uploads/2024/10/technology-report-2024-bain.pdf",
        "title": "Technology Report 2024",
    },
]

base = pathlib.Path("data/consulting_pdfs")

ok, fail = 0, 0
for item in PDFS:
    firm_dir = base / item["firm"]
    firm_dir.mkdir(parents=True, exist_ok=True)
    # derive filename from URL
    url_path = item["url"].split("?")[0]
    fname = pathlib.Path(url_path).name
    if not fname.endswith(".pdf"):
        fname = fname + ".pdf"
    dest = firm_dir / fname
    if dest.exists() and dest.stat().st_size > 1000:
        log.info("CACHED  %s / %s", item["firm"], fname)
        ok += 1
        continue
    try:
        req = urllib.request.Request(item["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 1000:
            raise ValueError(f"too small ({len(data)} bytes)")
        dest.write_bytes(data)
        log.info("OK      %s / %s  (%d KB)", item["firm"], fname, len(data) // 1024)
        ok += 1
    except Exception as e:
        log.warning("FAIL    %s / %s  — %s", item["firm"], fname, e)
        fail += 1
    time.sleep(0.5)

log.info("Done: %d downloaded, %d failed", ok, fail)
