"""Build the Visa Bulletin table from independent republications that agree.

travel.state.gov refuses every scripted client (403 on the whole domain, the PDFs, and
the Internet Archive's crawler since July 2026), so the official page cannot be read
automatically. Law firms and immigration platforms republish the charts the day the
bulletin appears, and their pages can be read. One republication is a trust problem —
Visa Lawyer Blog's October 2026 family chart has a truncated cell ("15MAY") — so a
cut-off is published only when at least MIN_AGREE independent sources state it and
none contradicts it. Everything else is left out, and the rule says "unconfirmed".

    python scripts/bulletin_update.py                 # next month's, else this month's
    python scripts/bulletin_update.py --month 2026-10 --out /tmp/vb.toml

Which chart USCIS accepts that month comes from uscis.gov, which is reachable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from bulletin_import import USCIS, uscis_chart  # noqa: E402

from visa import bulletin  # noqa: E402

MIN_AGREE = 2
UA = {"User-Agent": "visa-rag bulletin consensus (https://github.com/) Mozilla/5.0"}


@dataclass(frozen=True)
class Publisher:
    name: str
    url: str = ""  # {month} = "october", {year} = "2026"
    index: str = ""  # or: a page listing posts, searched with `link`
    link: str = ""
    order: tuple[str, ...] = ()  # chart order when the page does not label them


PUBLISHERS = (
    Publisher(
        "DiRaimondo & Schroeder LLP",
        index="https://www.diraimondoschroeder.com/updates",
        link=r'href="(/updates/\d{4}/\d{1,2}/\d{1,2}/[^"]*{month}-{year}-visa-bulletin[^"]*)"',
    ),
    Publisher(
        "Visa Lawyer Blog",
        url="https://www.visalawyerblog.com/{month}-{year}-visa-bulletin/",
        order=("final_action", "dates_for_filing"),  # headings are commentary, not labels
    ),
    Publisher(
        "Envoy Global",
        url="https://www.envoyglobal.com/news-alert/{month}-{year}-visa-bulletin/",
    ),
    Publisher(
        "Fisher Phillips",
        url="https://www.fisherphillips.com/en/insights/insights/the-fp-visa-bulletin-for-{month}-{year}",
    ),
)


def get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def locate(p: Publisher, month: dt.date) -> str | None:
    fill = {"month": month.strftime("%B").lower(), "year": str(month.year)}
    if p.url:
        return p.url.format(**fill)
    page = get(p.index)
    m = re.search(
        p.link.replace("{month}", fill["month"]).replace("{year}", fill["year"]), page
    )
    return p.index.split("/updates")[0] + m.group(1) if m else None


def read(
    p: Publisher, month: dt.date
) -> tuple[str, dict[str, dict[str, dict[str, str]]]]:
    """(url, {chart: {category: {area: cut-off}}}) from one publisher, or raise."""
    url = locate(p, month)
    if not url:
        raise LookupError("no post for this month found")
    page = get(url)
    label = month.strftime("%B %Y")
    if label.lower() not in bulletin._text(page).lower():
        raise LookupError(f"page does not mention {label}")
    charts: dict[str, dict[str, dict[str, str]]] = {}
    eb = [
        (before, rows)
        for before, rows in bulletin.tables(page)
        if (rows and bulletin.read_employment(rows, strict=False))
    ]
    for i, (before, rows) in enumerate(eb):
        chart = p.order[i] if i < len(p.order) else bulletin.chart_of(before)
        if chart and chart not in charts:
            charts[chart] = bulletin.read_employment(rows, strict=False)
    if not charts:
        raise LookupError("no employment-based chart found")
    return url, charts


def consensus(
    readings: dict[str, dict[str, dict[str, dict[str, str]]]],
) -> tuple[dict[str, dict[str, dict[str, str]]], list[str], set[tuple[str, str, str]]]:
    """Cells stated identically by >= MIN_AGREE sources and contradicted by none; notes;
    and the cells the sources now disagree on."""
    votes: dict[tuple[str, str, str], dict[str, str]] = {}
    for src, charts in readings.items():
        for chart, table in charts.items():
            for cat, row in table.items():
                for area, v in row.items():
                    votes.setdefault((chart, cat, area), {})[src] = v
    agreed: dict[str, dict[str, dict[str, str]]] = {c: {} for c in bulletin.CHARTS}
    notes = []
    disputed: set[tuple[str, str, str]] = set()
    for (chart, cat, area), by in sorted(votes.items()):
        values = set(by.values())
        if len(values) > 1:
            disputed.add((chart, cat, area))
            notes.append(f"DISAGREE {chart} {cat} {area}: {by}")
        elif len(by) < MIN_AGREE:
            notes.append(f"single source {chart} {cat} {area}: {by}")
        else:
            agreed[chart].setdefault(cat, {})[area] = values.pop()
    return agreed, notes, disputed


def merge_same_month(
    prev: dict[str, object],
    agreed: dict[str, dict[str, dict[str, str]]],
    disputed: set[tuple[str, str, str]],
) -> dict[str, dict[str, dict[str, str]]]:
    """Today's agreement wins; a cell confirmed earlier this month is kept unless today's
    sources dispute it. Run from GitHub, Visa Lawyer Blog answers 403 - a publisher
    going quiet must not delete what two others already confirmed."""
    out = {c: {cat: dict(row) for cat, row in agreed[c].items()} for c in bulletin.CHARTS}
    for c in bulletin.CHARTS:
        for cat, row in dict(prev.get(c) or {}).items():  # type: ignore[call-overload]
            for area, v in row.items():
                if (c, cat, area) not in disputed:
                    out[c].setdefault(cat, {}).setdefault(area, v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--month", help="YYYY-MM; default: next month's if published, else this"
    )
    ap.add_argument("--out", type=Path, default=REPO / "bulletin" / "visa_bulletin.toml")
    args = ap.parse_args()

    today = dt.date.today()
    nxt = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    candidates = (
        [dt.date(*map(int, args.month.split("-")), 1)]
        if args.month
        else [nxt, today.replace(day=1)]
    )
    for month in candidates:
        readings, urls = {}, {}
        for p in PUBLISHERS:
            try:
                urls[p.name], readings[p.name] = read(p, month)
                n = sum(len(r) for t in readings[p.name].values() for r in t.values())
                print(f"  {p.name}: {sorted(readings[p.name])} ({n} cells)")
            except Exception as e:  # a missing or changed page just does not vote
                print(f"  {p.name}: skipped ({e})")
        agreed, notes, disputed = consensus(readings)
        if any(agreed[c] for c in bulletin.CHARTS):
            break  # partial is fine: cells without agreement show as UNCONFIRMED
        print(f"{month:%B %Y}: not enough agreeing sources yet")
    else:
        print("no month with agreeing sources yet; table unchanged")
        return 0  # nothing new today is not a failure

    label = month.strftime("%B %Y")
    ym = month.strftime("%Y-%m")
    prev_data = tomllib.loads(args.out.read_text()) if args.out.exists() else {}
    prev = str(prev_data.get("month", ""))
    if prev and ym < prev:  # never replace a newer month with an older one
        print(f"{label} is older than the table's {prev}; table unchanged")
        return 0
    sources = [f"{n} <{u}>" for n, u in urls.items() if n in readings]
    if prev == ym:
        agreed = merge_same_month(prev_data, agreed, disputed)
        sources += [s for s in prev_data.get("sources", []) if s not in sources]
    chart = uscis_chart(label) or (
        prev_data.get("uscis_chart") or None if prev == ym else None
    )
    data: dict[str, object] = {"month": ym, **agreed}
    args.out.write_text(
        bulletin.to_toml(
            data,
            source_url="",
            uscis_chart=chart,
            uscis_url=USCIS,
            sources=sources,
            method=f"consensus of independent republications, >= {MIN_AGREE} agreeing",
        )
    )
    cells = sum(len(r) for t in agreed.values() for r in t.values())
    print(
        f"{label}: {cells} agreed cells -> {args.out}; "
        f"USCIS chart: {chart or 'not stated'}"
    )
    for n in notes:
        print("  ", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
