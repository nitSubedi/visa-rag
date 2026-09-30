"""Import one month's Visa Bulletin from a saved page. The monthly maintainer step.

travel.state.gov refuses scripted clients, so a person opens the bulletin in a browser,
saves the page (File > Save Page As, "Webpage, HTML Only"), and runs:

    python scripts/bulletin_import.py ~/Downloads/visa-bulletin-for-october-2026.html

The employment-based charts and the month are read from the page itself; which chart
USCIS accepts for adjustment of status that month is read from uscis.gov (reachable),
matched to the same month. Nothing is typed by hand. Writes bulletin/visa_bulletin.toml
unless --out is given.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from visa import bulletin  # noqa: E402

USCIS = (
    "https://www.uscis.gov/green-card/green-card-processes-and-procedures/"
    "visa-availability-priority-dates/adjustment-of-status-filing-charts-from-the-"
    "visa-bulletin"
)
SOURCE = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"


def uscis_chart(month_label: str) -> str | None:
    """'final_action' or 'dates_for_filing' for employment-based filings in that month,
    or None if uscis.gov does not (yet) say."""
    req = urllib.request.Request(USCIS, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", "ignore")
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    m = re.search(
        r"For Employment-Based Preference Filings: For all employment-based "
        r"preference categories, you must use the (Final Action Dates|Dates for Filing) "
        rf"chart in the Department of State Visa Bulletin for {re.escape(month_label)}",
        text,
    )
    if not m:
        return None
    return "final_action" if m.group(1).startswith("Final") else "dates_for_filing"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=Path, help="the saved bulletin page (.html)")
    ap.add_argument("--out", type=Path, default=REPO / "bulletin" / "visa_bulletin.toml")
    ap.add_argument("--no-uscis", action="store_true", help="skip the uscis.gov lookup")
    args = ap.parse_args()

    data = bulletin.parse_html(args.page.read_text(errors="ignore"))
    y, mo = (int(x) for x in str(data["month"]).split("-"))
    label = dt.date(y, mo, 1).strftime("%B %Y")
    chart = None if args.no_uscis else uscis_chart(label)
    args.out.write_text(
        bulletin.to_toml(
            data, SOURCE, chart, USCIS, sources=[SOURCE], method="official bulletin page"
        )
    )
    print(f"imported the {label} bulletin -> {args.out}")
    print(f"USCIS chart for employment-based adjustment: {chart or 'not stated yet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
