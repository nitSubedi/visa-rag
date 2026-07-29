"""Deterministic date arithmetic.

LLMs are unreliable at date math, and immigration deadlines are unforgiving. These
windows are computed in Python from the profile and handed to the model as facts to
explain — never calculated by it. Each carries the provision it derives from so the
answer layer can cite it and the user can verify.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

DAY = dt.timedelta(days=1)


@dataclass
class Window:
    name: str
    opens: dt.date | None
    closes: dt.date | None
    authority: str
    note: str = ""

    def status(self, today: dt.date | None = None) -> str:
        t = today or dt.date.today()
        if self.opens and t < self.opens:
            return f"opens in {(self.opens - t).days} days"
        if self.closes and t > self.closes:
            return f"CLOSED {(t - self.closes).days} days ago"
        if self.closes:
            return f"OPEN — {(self.closes - t).days} days remaining"
        return "open"

    def fmt(self, today: dt.date | None = None) -> str:
        a = self.opens.isoformat() if self.opens else "—"
        b = self.closes.isoformat() if self.closes else "—"
        return f"{self.name}: {a} → {b}  [{self.status(today)}]  ({self.authority})"


def post_completion_opt(program_end: dt.date) -> list[Window]:
    """8 CFR 214.2(f)(11)(i)(B)(2): the I-765 may be filed up to 90 days before the
    program end date and no later than 60 days after it (and within 30 days of the
    DSO's recommendation in SEVIS)."""
    return [
        Window("Post-completion OPT filing window",
               program_end - 90 * DAY, program_end + 60 * DAY,
               "8 CFR 214.2(f)(11)(i)(B)(2)",
               "Must also be filed within 30 days of the DSO's SEVIS recommendation."),
        Window("F-1 grace period after program end",
               program_end + DAY, program_end + 60 * DAY,
               "8 CFR 214.2(f)(5)(iv)",
               "60 days to depart, change status, or transfer — only if status was maintained."),
    ]


def stem_extension(opt_end: dt.date) -> Window:
    """8 CFR 214.2(f)(11)(i)(C): the 24-month STEM extension may be filed up to 90
    days before the current post-completion OPT employment authorization expires."""
    return Window("STEM OPT (24-mo) filing window",
                  opt_end - 90 * DAY, opt_end,
                  "8 CFR 214.2(f)(11)(i)(C)",
                  "Requires an E-Verify employer and a Form I-983 training plan.")


def opt_grace(opt_end: dt.date) -> Window:
    return Window("Grace period after OPT ends", opt_end + DAY, opt_end + 60 * DAY,
                  "8 CFR 214.2(f)(5)(iv)")


def unemployment_budget(opt_start: dt.date, stem: bool = False) -> Window:
    """Aggregate unemployment caps: 90 days on post-completion OPT, plus 60 more
    during a STEM extension (150 total)."""
    cap = 150 if stem else 90
    return Window(f"Aggregate unemployment allowance ({cap} days)",
                  opt_start, None, "8 CFR 214.2(f)(10)(ii)(E)",
                  "Exceeding the cap is a status violation, counted cumulatively.")


def cap_gap(fiscal_year: int) -> Window:
    """8 CFR 214.2(f)(5)(vi): F-1 status and any employment authorization are extended
    to Sep 30 when a timely H-1B cap petition requesting a change of status is filed
    while the student is in valid F-1 status or the 60-day grace period."""
    return Window(f"H-1B cap-gap extension (FY{fiscal_year})",
                  dt.date(fiscal_year - 1, 4, 1), dt.date(fiscal_year - 1, 9, 30),
                  "8 CFR 214.2(f)(5)(vi)",
                  "Requires a timely-filed, pending or approved cap-subject H-1B "
                  "requesting change of status with an Oct 1 start.")


def compute(profile: dict, today: dt.date | None = None) -> list[Window]:
    """Everything derivable from what the profile actually contains."""
    t = today or dt.date.today()
    out: list[Window] = []

    def d(key: str) -> dt.date | None:
        v = profile.get(key)
        if isinstance(v, dt.date):
            return v
        if isinstance(v, str) and v.strip():
            try:
                return dt.date.fromisoformat(v.strip())
            except ValueError:
                return None
        return None

    pe, oe, os_ = d("program_end_date"), d("opt_end_date"), d("opt_start_date")
    status = str(profile.get("status", "")).upper()

    if pe and status.startswith("F"):
        out += post_completion_opt(pe)
    if oe:
        out.append(stem_extension(oe))
        out.append(opt_grace(oe))
    if os_:
        out.append(unemployment_budget(os_, stem=bool(profile.get("stem_extension"))))
    if status.startswith("F"):
        fy = t.year + 1 if t.month >= 10 else t.year
        out.append(cap_gap(fy + 1 if t.month >= 4 else fy))
    return out


def render(profile: dict, today: dt.date | None = None) -> str:
    ws = compute(profile, today)
    if not ws:
        return ""
    t = (today or dt.date.today()).isoformat()
    lines = [f"COMPUTED DEADLINES (deterministic, from profile; today = {t}):"]
    for w in ws:
        lines.append("  " + w.fmt(today))
        if w.note:
            lines.append(f"      note: {w.note}")
    return "\n".join(lines)
