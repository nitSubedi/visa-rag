"""The Department of State Visa Bulletin, employment-based charts, as data.

A priority-date question is date arithmetic against a cutoff that changes monthly.
gemma3:4b and qwen answered it from memory ("June 1, 2021", "June 28, 2023"). The
comparison belongs in code and the cutoff in a table stamped with its month, so an
out-of-date table says so rather than answering wrongly with confidence.

The table is produced from the bulletin page itself by `parse_html` (see
scripts/bulletin_import.py) — never typed. The bulletin's own rule is encoded
verbatim: "Numbers are authorized for issuance only for applicants whose priority date
is earlier than the final action date listed below." "C" is current, "U" unauthorized.
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import settings

CHARTS = ("final_action", "dates_for_filing")

# Row labels as the bulletin prints them -> our keys.
ROWS = {
    "1st": "EB-1",
    "2nd": "EB-2",
    "3rd": "EB-3",
    "other workers": "EB-3 other workers",
    "4th": "EB-4",
    "certain religious workers": "EB-4 religious workers",
    "5th unreserved": "EB-5 unreserved",
}
# Column headers -> areas of chargeability.
AREAS = (
    ("china", r"china"),
    ("india", r"india"),
    ("mexico", r"mexico"),
    ("philippines", r"philippines"),
    ("all", r"all chargeability"),
)

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}  # jan -> 1


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_cell(cell: str) -> str:
    """'01JUN23' -> '2023-06-01'; 'C' and 'U' kept as they are."""
    c = cell.strip().upper()
    if c in ("C", "U"):
        return c
    m = re.fullmatch(r"(\d{2})([A-Z]{3})(\d{2})", c)
    if not m or m.group(2).lower() not in MONTHS:
        raise ValueError(f"unreadable bulletin cell: {cell!r}")
    year = 2000 + int(m.group(3))
    return dt.date(year, MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()


def parse_html(page: str) -> dict[str, object]:
    """Read the month and both employment-based charts from a saved bulletin page."""
    title = re.search(r"Visa Bulletin For ([A-Z][a-z]+) (\d{4})", _text(page))
    if not title:
        raise ValueError("no 'Visa Bulletin For <Month> <Year>' title on the page")
    month = dt.datetime.strptime(f"{title.group(1)} {title.group(2)}", "%B %Y").date()
    out: dict[str, object] = {"month": month.strftime("%Y-%m")}
    for m in re.finditer(r"<table.*?</table>", page, re.S | re.I):
        rows = [
            [_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S | re.I)]
            for r in re.findall(r"<tr.*?</tr>", m.group(0), re.S | re.I)
        ]
        if not rows or not rows[0] or not rows[0][0].lower().startswith("employment"):
            continue
        before = _text(page[max(0, m.start() - 6000) : m.start()]).upper()
        fa = before.rfind("FINAL ACTION DATES FOR EMPLOYMENT-BASED")
        df = before.rfind("DATES FOR FILING OF EMPLOYMENT-BASED")
        chart = "final_action" if fa > df else "dates_for_filing"
        cols = []
        for h in rows[0][1:]:
            key = next((k for k, rx in AREAS if re.search(rx, h, re.I)), None)
            if key is None:
                raise ValueError(f"unknown chargeability column: {h!r}")
            cols.append(key)
        table: dict[str, dict[str, str]] = {}
        for r in rows[1:]:
            label = r[0].lower()
            key = next((v for k, v in ROWS.items() if label.startswith(k)), None)
            if key is None:
                continue  # 5th set-asides: not in this tool's scope
            table[key] = {a: parse_cell(c) for a, c in zip(cols, r[1:], strict=True)}
        out[chart] = table
    missing = [c for c in CHARTS if c not in out]
    if missing:
        raise ValueError(f"employment-based chart(s) not found: {missing}")
    return out


@dataclass(frozen=True)
class Bulletin:
    month: dt.date  # first of the bulletin's month
    charts: dict[str, dict[str, dict[str, str]]]
    uscis_chart: str | None  # which chart USCIS accepts for EB adjustment this month
    source_url: str
    uscis_url: str

    @property
    def label(self) -> str:
        return self.month.strftime("%B %Y")

    def is_current(self, today: dt.date | None = None) -> bool:
        t = today or dt.date.today()
        return (self.month.year, self.month.month) == (t.year, t.month)

    def cutoff(self, chart: str, category: str, area: str) -> str | None:
        return self.charts.get(chart, {}).get(category, {}).get(area)


def _path() -> Path:
    fetched = settings.bulletin / "visa_bulletin.toml"
    return fetched if fetched.exists() else settings.bulletin_defs / "visa_bulletin.toml"


def load() -> Bulletin | None:
    """None when no month has been imported — the rule then says so, never guesses."""
    p = _path()
    if not p.exists():
        return None
    d = tomllib.loads(p.read_text())
    if not d.get("month"):
        return None
    y, m = (int(x) for x in str(d["month"]).split("-"))
    return Bulletin(
        month=dt.date(y, m, 1),
        charts={c: d.get(c, {}) for c in CHARTS},
        uscis_chart=d.get("uscis_chart") or None,
        source_url=str(d.get("source_url", "")),
        uscis_url=str(d.get("uscis_url", "")),
    )


def compare(priority_date: dt.date, cutoff: str) -> str:
    """CURRENT, NOT CURRENT or UNAVAILABLE, by the bulletin's own rule."""
    if cutoff == "C":
        return "CURRENT"
    if cutoff == "U":
        return "UNAVAILABLE"
    return "CURRENT" if priority_date < dt.date.fromisoformat(cutoff) else "NOT CURRENT"


def to_toml(
    data: dict[str, object], source_url: str, uscis_chart: str | None, uscis_url: str
) -> str:
    lines = [
        "# Visa Bulletin, employment-based charts. Generated by",
        "# scripts/bulletin_import.py from the saved bulletin page.",
        "# Do not edit by hand.",
        f'month = "{data["month"]}"',
        f'source_url = "{source_url}"',
        f'uscis_chart = "{uscis_chart or ""}"',
        f'uscis_url = "{uscis_url}"',
    ]
    for chart in CHARTS:
        for cat, row in data[chart].items():  # type: ignore[attr-defined]
            lines.append(f'\n[{chart}."{cat}"]')
            lines += [f'{area} = "{v}"' for area, v in row.items()]
    return "\n".join(lines) + "\n"
