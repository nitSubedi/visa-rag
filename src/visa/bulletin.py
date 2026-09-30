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

# Row labels -> our keys, in the forms the bulletin and its republishers print them
# ("1st", "EB-1", "EB-3 Professionals/Skilled Workers"). Order matters: "Other
# Workers" must be tried before "3rd"/"EB-3", which some sources prefix it with.
ROWS = (
    (r"other workers", "EB-3 other workers"),
    (r"religious", "EB-4 religious workers"),
    (r"^(1st|eb-?1)\b", "EB-1"),
    (r"^(2nd|eb-?2)\b", "EB-2"),
    (r"^(3rd|eb-?3)\b", "EB-3"),
    (r"^(4th|eb-?4)\b", "EB-4"),
    (r"^(5th|eb-?5) unreserved", "EB-5 unreserved"),
)
# Column headers -> areas of chargeability.
AREAS = (
    ("china", r"china"),
    ("india", r"india"),
    ("mexico", r"mexico"),
    ("philippines", r"philippines"),
    ("all", r"all chargeability|all others|worldwide|rest of world|\brow\b"),
)

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}  # jan -> 1


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_cell(cell: str) -> str:
    """'01JUN23' or 'June 1, 2023' -> '2023-06-01'; 'C'/'Current' -> 'C';
    'U'/'Unavailable' -> 'U'. Anything else raises."""
    c = cell.strip().rstrip("*").strip()
    if c.upper() in ("C", "CURRENT"):
        return "C"
    if c.upper() in ("U", "UNAVAILABLE", "UNAUTHORIZED"):
        return "U"
    m = re.fullmatch(r"(\d{2})([A-Za-z]{3})(\d{2})", c)
    if m and m.group(2).lower() in MONTHS:
        return dt.date(
            2000 + int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))
        ).isoformat()
    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b. %d, %Y"):
        try:
            return dt.datetime.strptime(c, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unreadable bulletin cell: {cell!r}")


def tables(page: str) -> list[tuple[str, list[list[str]]]]:
    """Each table on a page, with the text that precedes it."""
    out = []
    for m in re.finditer(r"<table.*?</table>", page, re.S | re.I):
        rows = [
            [_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S | re.I)]
            for r in re.findall(r"<tr.*?</tr>", m.group(0), re.S | re.I)
        ]
        out.append((_text(page[max(0, m.start() - 6000) : m.start()]), rows))
    return out


def read_employment(
    rows: list[list[str]], strict: bool = True
) -> dict[str, dict[str, str]]:
    """{category: {area: cut-off}} from one employment-based chart, or {} if the table is
    not one. Columns that are not an area (e.g. "Notes") are ignored. With strict=False
    an unreadable cell is dropped rather than raising — a republisher's typo ("15MAY")
    then simply does not vote."""
    if not rows or not rows[0]:
        return {}
    cols: list[str | None] = [
        next((k for k, rx in AREAS if re.search(rx, h, re.I)), None) for h in rows[0][1:]
    ]
    if not any(cols):
        return {}
    table: dict[str, dict[str, str]] = {}
    for r in rows[1:]:
        if not r:
            continue
        key = next((v for rx, v in ROWS if re.search(rx, r[0].strip(), re.I)), None)
        if key is None:
            continue  # 5th set-asides and family rows: not in this tool's scope
        cells: dict[str, str] = {}
        for area, c in zip(cols, r[1:], strict=False):
            if area is None:
                continue
            try:
                cells[area] = parse_cell(c)
            except ValueError:
                if strict:
                    raise
        if cells:
            table[key] = cells
    return table


def chart_of(before: str) -> str | None:
    """Which chart a table is, from the heading text just before it, or None."""
    b = before.upper()[-1500:]
    fa, df = (
        b.rfind("FINAL ACTION"),
        max(b.rfind("DATES FOR FILING"), b.rfind("FILING DATES")),
    )
    if fa < 0 and df < 0:
        return None
    return "final_action" if fa > df else "dates_for_filing"


def parse_html(page: str) -> dict[str, object]:
    """Read the month and both employment-based charts from the official bulletin page."""
    title = re.search(r"Visa Bulletin For ([A-Z][a-z]+) (\d{4})", _text(page))
    if not title:
        raise ValueError("no 'Visa Bulletin For <Month> <Year>' title on the page")
    month = dt.datetime.strptime(f"{title.group(1)} {title.group(2)}", "%B %Y").date()
    out: dict[str, object] = {"month": month.strftime("%Y-%m")}
    for before, rows in tables(page):
        if not rows or not rows[0] or not rows[0][0].lower().startswith("employment"):
            continue
        u = before.upper()
        fa = u.rfind("FINAL ACTION DATES FOR EMPLOYMENT-BASED")
        df = u.rfind("DATES FOR FILING OF EMPLOYMENT-BASED")
        out["final_action" if fa > df else "dates_for_filing"] = read_employment(rows)
    missing = [c for c in CHARTS if c not in out]
    if missing:
        raise ValueError(f"employment-based chart(s) not found: {missing}")
    return out


@dataclass(frozen=True)
class Bulletin:
    month: dt.date  # first of the bulletin's month
    charts: dict[str, dict[str, dict[str, str]]]
    uscis_chart: str | None  # which chart USCIS accepts for EB adjustment this month
    sources: tuple[str, ...]
    method: str
    uscis_url: str

    @property
    def label(self) -> str:
        return self.month.strftime("%B %Y")

    def is_current(self, today: dt.date | None = None) -> bool:
        t = today or dt.date.today()
        return (self.month.year, self.month.month) == (t.year, t.month)

    def timing(self, today: dt.date | None = None) -> str:
        """'current', 'upcoming' (published ahead, not yet in effect) or 'stale'."""
        t = (today or dt.date.today()).replace(day=1)
        if self.month == t:
            return "current"
        return "upcoming" if self.month > t else "stale"

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
        sources=tuple(str(x) for x in d.get("sources", [])),
        method=str(d.get("method", "")),
        uscis_url=str(d.get("uscis_url", "")),
    )


def compare(priority_date: dt.date, cutoff: str) -> str:
    """CURRENT, NOT CURRENT or UNAVAILABLE, by the bulletin's own rule."""
    if cutoff == "C":
        return "CURRENT"
    if cutoff == "U":
        return "UNAVAILABLE"
    return "CURRENT" if priority_date < dt.date.fromisoformat(cutoff) else "NOT CURRENT"


def _q(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_toml(
    data: dict[str, object],
    source_url: str,
    uscis_chart: str | None,
    uscis_url: str,
    sources: list[str] | None = None,
    method: str = "official bulletin page",
) -> str:
    lines = [
        "# Visa Bulletin, employment-based charts. Generated by scripts/ - do not edit.",
        f"month = {_q(str(data['month']))}",
        f"method = {_q(method)}",
        f"sources = [{', '.join(_q(x) for x in (sources or [source_url]))}]",
        f"uscis_chart = {_q(uscis_chart or '')}",
        f"uscis_url = {_q(uscis_url)}",
    ]
    for chart in CHARTS:
        for cat, row in data[chart].items():  # type: ignore[attr-defined]
            lines.append(f"\n[{chart}.{_q(cat)}]")
            lines += [f"{area} = {_q(v)}" for area, v in row.items()]
    return "\n".join(lines) + "\n"
