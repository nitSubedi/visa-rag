"""Eligibility rules the code decides, so the model does not have to.

A claim-level audit of the held-out answers (STATE.md finding 28) found about two wrong
statements per answer, and most were not invented text but wrong applications of the
right text: 12 months of full-time CPT treated as pre-completion OPT, so "the student is
eligible for post-completion OPT". No checker a 4b can run caught them, including the
model judging itself. `dates.py` already showed the way out — restating dates Python
computed scores 100%, deriving them did not — so the rules a person's status turns on
are computed here, the same way, and handed to the model as settled.

Every rule quotes its sources verbatim; tests/test_rules.py checks each quote against the
served corpus, as terminology.py does. A rule that cannot be decided from the facts
given says which fact it needs — that is a question to ask the person, never a guess.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    citation: str
    quote: str  # verbatim, checked against the served corpus


@dataclass(frozen=True)
class Outcome:
    rule: str
    decision: str  # the conclusion, stated for this person
    because: str  # the facts it rests on
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class Needs:
    rule: str
    fact: str  # a Fact key
    question: str  # how to ask the person for it


@dataclass(frozen=True)
class Fact:
    key: str
    kind: str  # JSON-schema type for extraction
    describe: str  # what to extract, for the extraction prompt
    stated: Callable[[object, str], bool]  # is this value literally in the person's words


_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-four": 24,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "a hundred": 100,
    "one hundred": 100,
}
# Compounds ("sixty-five") are not read. That fails safe: the number cannot be
# confirmed, so the rule asks.


def _months_stated(value: object, text: str) -> bool:
    """A month count the person actually wrote, about CPT they said was full-time.

    Extraction invented one: "I did some CPT last year" came back as 12 full-time
    months, and the rule then told the person, with quoted law, that they were
    ineligible for OPT. A fact a rule rests on must be in the person's own words —
    as a numeral, a number word, or "a/one year" for 12 — and "full-time" must be
    said. Anything less is asked, not assumed."""
    try:
        v = float(str(value))
    except ValueError:
        return False
    t = text.lower()
    if not re.search(r"full[- ]?time", t):
        return False
    said = _said_numbers(t)
    if re.search(r"\b(a|one|1) (full |whole )?year\b", t):
        said.add(12.0)
    return v in said


def _said_numbers(t: str) -> set[float]:
    said = {float(n) for n in re.findall(r"\b\d+(?:\.\d+)?\b", t)}
    return said | {float(n) for w, n in _WORDS.items() if re.search(rf"\b{w}\b", t)}


def _days_stated(value: object, text: str) -> bool:
    """Every day count is one the person wrote, in a message about unemployment."""
    t = text.lower()
    if not isinstance(value, list) or not value or "unemploy" not in t:
        return False
    said = _said_numbers(t)
    try:
        return all(float(str(v)) in said for v in value)
    except ValueError:
        return False


@dataclass(frozen=True)
class Rule:
    id: str
    applies: Callable[[dict[str, object]], bool]  # is this rule in play at all
    decide: Callable[[dict[str, object]], Outcome | Needs]
    sources: tuple[Source, ...]


FACTS: tuple[Fact, ...] = (
    Fact(
        "cpt_full_time_months",
        "number",
        "Months of FULL-TIME curricular practical training (CPT) the person says they "
        "have done at their current degree level. 'a year' = 12. Null if not stated, "
        "or if the CPT was part-time.",
        _months_stated,
    ),
    Fact(
        "unemployment_days",
        "array",
        "Each number of days of unemployment the person says they have had during OPT "
        "or STEM OPT, one entry per period they mention. Null if they state none.",
        _days_stated,
    ),
)


def _num(v: object) -> float | None:
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        return float(str(v))
    except ValueError:
        return None


# --- full-time CPT bars post-completion OPT ---------------------------------------

CPT_SOURCES = (
    Source(
        "8 CFR § 214.2(f)(10)(i)",
        "Students who have received one year or more of full time curricular practical "
        "training are ineligible for post-completion academic training.",
    ),
    Source(
        "USCIS PM Vol 2, Pt F, Ch 5, B",
        "An F-1 student who has received 1 year or more of full-time CPT is ineligible "
        "for post-completion OPT at the same educational level.",
    ),
)


def _cpt_applies(f: dict[str, object]) -> bool:
    return bool(f.get("mentions_cpt")) or _num(f.get("cpt_full_time_months")) is not None


def _cpt_decide(f: dict[str, object]) -> Outcome | Needs:
    months = _num(f.get("cpt_full_time_months"))
    if months is None:
        return Needs(
            "cpt_full_time_year",
            "cpt_full_time_months",
            "How many months of full-time CPT have you done at your current degree "
            "level? (Part-time CPT does not count toward this limit.)",
        )
    if months >= 12:
        return Outcome(
            "cpt_full_time_year",
            "INELIGIBLE for post-completion OPT at the same educational level as the "
            "CPT.",
            f"{months:g} months of full-time CPT is one year or more.",
            CPT_SOURCES,
        )
    return Outcome(
        "cpt_full_time_year",
        "NOT barred from post-completion OPT by the full-time CPT limit (the other "
        "OPT requirements still apply).",
        f"{months:g} months of full-time CPT is less than one year.",
        CPT_SOURCES,
    )


# --- the aggregate unemployment limit ----------------------------------------------

UNEMPLOYMENT_SOURCES = (
    Source(
        "8 CFR § 214.2(f)(10)(ii)(E)",
        "Students may not accrue an aggregate of more than 90 days of unemployment "
        "during any post-completion OPT period described in 8 CFR 274a.12(c)(3)(i)(B).",
    ),
    Source(
        "8 CFR § 214.2(f)(10)(ii)(E)",
        "may not accrue an aggregate of more than 150 days of unemployment during a "
        "total OPT period, including any post-completion OPT period described in 8 CFR "
        "274a.12(c)(3)(i)(B) and any subsequent 24-month extension period.",
    ),
)


def _unemployment_applies(f: dict[str, object]) -> bool:
    return bool(f.get("mentions_unemployment")) or bool(f.get("unemployment_days"))


def _on_stem(f: dict[str, object]) -> bool | None:
    """From the question if it says so, else the profile; None if neither does."""
    if f.get("mentions_stem"):
        return True
    v = f.get("stem_extension")
    return v if isinstance(v, bool) else None


def _unemployment_decide(f: dict[str, object]) -> Outcome | Needs:
    rule = "unemployment_limit"
    days = f.get("unemployment_days")
    if not isinstance(days, list) or not days:
        return Needs(
            rule,
            "unemployment_days",
            "How many days of unemployment have you had in total during OPT (and STEM "
            "OPT, if you are on it)?",
        )
    stem = _on_stem(f)
    if stem is None:
        return Needs(rule, "stem_extension", "Are you on a 24-month STEM OPT extension?")
    limit = 150 if stem else 90
    used = sum(float(str(d)) for d in days)
    parts = " + ".join(f"{float(str(d)):g}" for d in days)
    total = f"{parts} = {used:g}" if len(days) > 1 else f"{used:g}"
    period = (
        "a total OPT period including a STEM extension" if stem else "post-completion OPT"
    )
    if used > limit:
        decision = (
            f"OVER THE LIMIT: {used:g} days of unemployment exceeds the {limit}-day "
            f"aggregate allowed during {period}."
        )
    else:
        decision = (
            f"{limit - used:g} days of unemployment REMAIN of the {limit}-day aggregate "
            f"allowed during {period}."
        )
    return Outcome(
        rule,
        decision,
        f"days used: {total}; limit {limit} ({'on' if stem else 'not on'} a STEM "
        f"extension).",
        UNEMPLOYMENT_SOURCES[1:] if stem else UNEMPLOYMENT_SOURCES[:1],
    )


# --- stated situations -------------------------------------------------------------
# Yes/no facts read straight from the person's words, so they are stated by
# construction. A pattern that does not match means "not said", which a rule turns
# into a question, never into a default.

_SITUATIONS = {
    "says_cos": r"change (of|in) (nonimmigrant )?status|\bCOS\b",
    "says_pending": r"pending|not (yet )?(been )?(approved|decided)|"
    r"hasn.t (been )?(approved|decided)|waiting|no decision",
    "says_decided": r"\b(approved|denied|rejected)\b(?![^.]{0,20}\byet\b)",
    "says_travel": r"travel|abroad|leave the (U\.?S|country|United States)|outside the "
    r"(U\.?S|United States)|go home|trip",
    "says_stem_filed": r"(filed|applied|submitted)[^.]{0,40}STEM|STEM[^.]{0,40}(filed|"
    r"applied|submitted|application|pending)",
    "says_timely": r"on time|timely|before (my|the) (OPT |EAD |OPT EAD )?(card )?"
    r"expir",
    "says_ead_expired": r"(EAD|OPT|card)[^.]{0,30}(expired|ran out|ended)",
    "says_priority_date": r"priority date|visa bulletin|final action date|"
    r"dates for filing|retrogress|cut-?off date|\bcurrent for\b",
}


def read_situation(question: str) -> dict[str, object]:
    out: dict[str, object] = {
        k: True for k, rx in _SITUATIONS.items() if re.search(rx, question, re.IGNORECASE)
    }
    if out.get("says_priority_date"):
        if cat := category_of(question):
            out["stated_category"] = cat
        if d := date_of(question):
            out["stated_priority_date"] = d
    return out


# --- travel while a change of status is pending -----------------------------------
# Not 8 CFR 248.1(f): it says the same thing but was added by the enjoined rule and is
# withheld. The Policy Manual statements predate that rule and are in force.

COS_TRAVEL_SOURCES = (
    Source(
        "USCIS PM Vol 2, Pt F, Ch 8, A.5",
        "If a nonimmigrant travels abroad while their COS application is pending, USCIS "
        "considers that COS application abandoned.",
    ),
    Source(
        "USCIS PM Vol 2, Pt F, Ch 5, D.3",
        "If an F-1 student travels abroad while the application for change of status to "
        "H-1B is still pending, the change of status portion of the petition is deemed "
        "abandoned.",
    ),
)


def _cos_travel_applies(f: dict[str, object]) -> bool:
    return bool(f.get("says_cos") and f.get("says_travel"))


def _cos_travel_decide(f: dict[str, object]) -> Outcome | Needs:
    rule = "cos_travel_abandonment"
    if not f.get("says_pending") or f.get("says_decided"):
        return Needs(
            rule,
            "cos_pending",
            "Is your change of status application still pending (not yet approved or "
            "denied)?",
        )
    return Outcome(
        rule,
        "Travelling abroad while the change of status is pending ABANDONS the change of "
        "status request.",
        "the change of status is pending and the question is about travelling abroad.",
        COS_TRAVEL_SOURCES,
    )


# --- working while a STEM OPT extension is pending ----------------------------------

STEM_PENDING_SOURCES = (
    Source(
        "USCIS PM Vol 2, Pt F, Ch 5, C.5",
        "A student who has timely and properly filed a Form I-765 for the 24-month STEM "
        "OPT extension may continue working until the date of the USCIS written "
        "decision on the current Form I-765 or for up to 180 days after the "
        "student\u2019s current post-completion OPT expires, whichever is earlier.",
    ),
)


def _stem_pending_applies(f: dict[str, object]) -> bool:
    return bool(
        f.get("says_stem_filed") and (f.get("says_ead_expired") or f.get("says_pending"))
    )


def _stem_pending_decide(f: dict[str, object]) -> Outcome | Needs:
    rule = "stem_pending_work"
    if f.get("says_decided"):
        return Needs(
            rule, "stem_decided", "Has USCIS already decided your STEM OPT extension?"
        )
    if not f.get("says_timely"):
        return Needs(
            rule,
            "stem_timely",
            "Did you file the STEM OPT extension before your OPT EAD expired, after your "
            "DSO's recommendation?",
        )
    until = "180 days after your post-completion OPT expired"
    end = f.get("opt_end_date")
    try:
        d = end if isinstance(end, dt.date) else dt.date.fromisoformat(str(end))
        until = (
            f"{(d + dt.timedelta(days=180)).isoformat()} (180 days after {d.isoformat()})"
        )
    except (TypeError, ValueError):
        pass
    return Outcome(
        rule,
        "You MAY CONTINUE WORKING while the timely filed STEM OPT extension is pending — "
        f"until USCIS decides, or until {until}, whichever is earlier.",
        "the STEM OPT extension was filed on time and USCIS has not decided it.",
        STEM_PENDING_SOURCES,
    )


# --- is my priority date current? ---------------------------------------------------
# The cut-off comes from the Visa Bulletin table (bulletin.py), stamped with its month;
# the comparison is the Policy Manual's, done here. With no table loaded the rule says
# so and points to the source — it never supplies a date.

PRIORITY_SOURCES = (
    Source(
        "USCIS PM Vol 7, Pt A, Ch 6",
        "Visas are available for a prospective immigrant when the immigrant\u2019s "
        "priority date is earlier than the cut-off date shown in the relevant Visa "
        "Bulletin chart for his or her preference category and country of birth (and "
        "chargeability).",
    ),
    Source(
        "USCIS PM Vol 7, Pt A, Ch 7, F.4",
        "USCIS designates one of the two charts for use by aliens each month.",
    ),
)

_CATEGORY = (
    (r"\bEB-?1[ABC]?\b|first[- ]preference|\b1st preference", "EB-1"),
    (
        r"\bEB-?2\b|\bNIW\b|national interest waiver|second[- ]preference|"
        r"\b2nd preference",
        "EB-2",
    ),
    (r"\bEB-?3\b|third[- ]preference|\b3rd preference", "EB-3"),
    (r"\bEB-?4\b|fourth[- ]preference", "EB-4"),
)


def category_of(text: str) -> str | None:
    """The one employment-based category the text names, or None if none or several."""
    found = {c for rx, c in _CATEGORY if re.search(rx, text, re.IGNORECASE)}
    return found.pop() if len(found) == 1 else None


def area_of(country: str) -> str | None:
    """Area of chargeability as the bulletin columns name them, from country of birth.
    Mainland China only: Hong Kong, Macau and Taiwan are charged to "all"."""
    c = country.strip().lower()
    if not c:
        return None
    if re.search(r"hong kong|macau|macao|taiwan", c):
        return "all"
    for area, rx in (
        ("china", r"china|\bprc\b"),
        ("india", r"india"),
        ("mexico", r"mexic"),
        ("philippines", r"philippin|filipin"),
    ):
        if re.search(rx, c):
            return area
    return "all"


def date_of(text: str) -> dt.date | None:
    """A full date the person wrote: 2021-03-15, March 15 2021, 15 March 2021."""
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    for fmt in ("%B %d %Y", "%d %B %Y", "%b %d %Y", "%d %b %Y"):
        for cand in re.findall(
            r"[A-Za-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2} [A-Za-z]{3,9}\.? \d{4}", text
        ):
            try:
                return dt.datetime.strptime(
                    cand.replace(",", "").replace(".", ""), fmt
                ).date()
            except ValueError:
                continue
    return None


def _priority_applies(f: dict[str, object]) -> bool:
    return bool(f.get("says_priority_date"))


def _priority_decide(f: dict[str, object]) -> Outcome | Needs:
    from . import bulletin as vb  # local: bulletin imports config, rules must stay light

    rule = "priority_date_current"
    b = vb.load()
    if b is None:
        return Outcome(
            rule,
            "NO VISA BULLETIN IS LOADED, so this cannot be decided here. Compare your "
            "priority date with this month's bulletin at travel.state.gov, using the "
            "chart USCIS names on its Adjustment of Status Filing Charts page.",
            "no bulletin table has been imported.",
            PRIORITY_SOURCES,
        )
    cat = f.get("stated_category") or category_of(str(f.get("eb_category") or ""))
    if not cat:
        return Needs(
            rule,
            "eb_category",
            "Which employment-based category is your petition in (EB-1, EB-2, "
            "EB-3, or EB-4)?",
        )
    area = area_of(str(f.get("country_of_birth") or ""))
    if not area:
        return Needs(
            rule,
            "country_of_birth",
            "What is your country of birth? The Visa Bulletin charts by country of "
            "birth, not citizenship.",
        )
    pd = f.get("stated_priority_date") or date_of(str(f.get("priority_date") or ""))
    if not isinstance(pd, dt.date):
        return Needs(
            rule,
            "priority_date",
            "What is your priority date (on your I-140 or PERM receipt, e.g. "
            "2021-03-15)?",
        )
    parts = []
    for chart, name in (
        ("final_action", "Final Action Dates"),
        ("dates_for_filing", "Dates for Filing"),
    ):
        cut = b.cutoff(chart, str(cat), area)
        if cut is None:
            parts.append(f"{name}: not in the table")
            continue
        shown = {"C": "Current", "U": "Unavailable"}.get(cut, cut)
        parts.append(f"{name}: {vb.compare(pd, cut)} (cut-off {shown})")
    which = {"final_action": "Final Action Dates", "dates_for_filing": "Dates for Filing"}
    chart_note = (
        f" For {b.label}, USCIS accepts the {which[b.uscis_chart]} chart for "
        "employment-based adjustment of status."
        if b.uscis_chart in which
        else " Check which chart USCIS accepts this month on its filing charts page."
    )
    stale = (
        ""
        if b.is_current()
        else f"THIS IS THE {b.label.upper()} BULLETIN, NOT THIS MONTH'S — dates may have "
        "moved. "
    )
    return Outcome(
        rule,
        f"{stale}{cat}, {area} chargeability, priority date {pd.isoformat()} — "
        + "; ".join(parts)
        + "."
        + chart_note,
        f"{b.label} Visa Bulletin, employment-based charts.",
        PRIORITY_SOURCES,
    )


RULES: tuple[Rule, ...] = (
    Rule("cpt_full_time_year", _cpt_applies, _cpt_decide, CPT_SOURCES),
    Rule(
        "unemployment_limit",
        _unemployment_applies,
        _unemployment_decide,
        UNEMPLOYMENT_SOURCES,
    ),
    Rule(
        "cos_travel_abandonment",
        _cos_travel_applies,
        _cos_travel_decide,
        COS_TRAVEL_SOURCES,
    ),
    Rule(
        "stem_pending_work",
        _stem_pending_applies,
        _stem_pending_decide,
        STEM_PENDING_SOURCES,
    ),    Rule(
        "priority_date_current",
        _priority_applies,
        _priority_decide,
        PRIORITY_SOURCES,
    ),
)


def evaluate(facts: dict[str, object]) -> list[Outcome | Needs]:
    return [r.decide(facts) for r in RULES if r.applies(facts)]


PROMPT_HEAD = (
    "RULE FINDINGS (decided by code from the law and the person's facts — "
    "authoritative). Open your answer with each finding: state the conclusion, quote the "
    "law given for it with its citation, and say which of the person's facts it rests "
    "on. Never reverse it:"
)


def render(results: list[Outcome | Needs], head: str = PROMPT_HEAD) -> str:
    """The findings block. In the prompt it goes with the facts, like COMPUTED
    DEADLINES: it overrides passages rather than competing with them. Measured on three
    CPT questions: told only to "state these conclusions exactly", gemma3:4b answered
    "No." — right, and useless; told to open with the finding and its law, it gave the
    conclusion on all three. The CLI prints the same block itself, so the person sees
    the finding and its law whether or not the model restates them."""
    outcomes = [r for r in results if isinstance(r, Outcome)]
    if not outcomes:
        return ""
    lines = [head] if head else []
    for o in outcomes:
        lines.append(f"  {o.decision}")
        lines.append(f"      because: {o.because}")
        for s in o.sources:
            lines.append(f'      {s.citation}: "{s.quote}"')
    return "\n".join(lines)


def questions(results: list[Outcome | Needs]) -> list[str]:
    """What the person must be asked before a rule in play can be decided."""
    return [r.question for r in results if isinstance(r, Needs)]


_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_NEGATED = re.compile(
    r"\b(ineligible|not eligible|cannot|can't|isn't|is not|are not|aren't|no longer)\b",
    re.I,
)


def contradictions(
    text: str, results: list[Outcome | Needs], question: str = ""
) -> list[str]:
    """Where the model's prose disagrees with what the code decided.

    Told the finding and told never to reverse it, gemma3:4b still answered the
    held-out unemployment question "80 days." with "40 days ... REMAIN" in its prompt
    — it redid the arithmetic and dropped the second period. Wording will not make a
    4b reliable at restating, so the disagreement is detected and shown instead. A
    number or a verdict the code did not produce, stated about the thing a rule
    decided, is reported against the finding."""
    out: list[str] = []
    sents = [s.strip() for s in _SENT.split(text) if s.strip()]
    for r in results:
        if not isinstance(r, Outcome):
            continue
        if r.rule == "unemployment_limit":
            allowed = _said_numbers(f"{r.decision} {r.because} {question}".lower())
            m = re.match(r"(\d+(?:\.\d+)?) days", r.decision)  # None when over the limit
            remainder = float(m.group(1)) if m else None
            for s in sents:
                # Every day count, except in sentences plainly about another period:
                # the observed error was the whole answer, "80 days.", with no
                # unemployment word to scope on.
                if re.search(
                    r"grace|depart|window|fil(e|ing)|recommend|travel|abroad|report|"
                    r"within|before|after the",
                    s,
                    re.I,
                ):
                    continue
                # About what is left, only the computed remainder is right: "150 days
                # ... remaining" used the limit, a number the question also contains.
                about_left = bool(re.search(r"remain|\bleft\b", s, re.I))
                for n in re.findall(r"\b(\d+)\s+(?:more\s+|remaining\s+)?days?\b", s):
                    wrong_left = (
                        about_left and remainder is not None and float(n) != remainder
                    )
                    if float(n) not in allowed or wrong_left:
                        out.append(
                            f'the answer says "{n} days"; the rule computed: {r.decision}'
                        )
        elif r.rule == "cos_travel_abandonment":
            for s in sents:
                if re.search(r"\b(not|n't|never)\b[^.]{0,25}abandon", s, re.I):
                    head = " ".join(s.split())[:90]
                    out.append(
                        f'the answer says "{head}"; the rule decided: {r.decision}'
                    )
                    break
        elif r.rule == "stem_pending_work":
            for s in sents:
                if re.search(
                    r"not (be )?authorized to work|(cannot|can't|must not|may not) "
                    r"(continue |keep )?work|must stop working|stop working",
                    s,
                    re.I,
                ):
                    head = " ".join(s.split())[:90]
                    out.append(
                        f'the answer says "{head}"; the rule decided: {r.decision}'
                    )
                    break
        elif r.rule == "cpt_full_time_year":
            barred = r.decision.startswith("INELIGIBLE")
            for s in sents:
                if not re.search(r"eligib|\bOPT\b", s):
                    continue
                if not re.search(
                    r"eligible|can (still )?(get|apply|request|do)", s, re.I
                ):
                    continue
                says_barred = bool(_NEGATED.search(s))
                if says_barred != barred:
                    head = " ".join(s.split())[:90]
                    out.append(
                        f'the answer says "{head}"; the rule decided: {r.decision}'
                    )
                    break
    return out


# --- answers to follow-up questions --------------------------------------------------
# A rule that needs a fact asks for it (Needs.question). The reply is parsed here, by
# code: a yes/no, or the numbers the person typed. Anything else is treated as no
# answer, and the rule stays undecided rather than guessing.

# Facts about the person rather than about this question: saved to the profile, so the
# second question does not ask again.
PERSIST = {
    "cpt_full_time_months",
    "stem_extension",
    "eb_category",
    "country_of_birth",
    "priority_date",
}

_YES = re.compile(r"^\s*(y|yes|yeah|yep|correct|true|i am|i did|it is)\b", re.I)
_NO = re.compile(
    r"^\s*(n|no|nope|not|false|i'?m not|i did not|i didn'?t|it isn'?t)\b", re.I
)


def _yes_no(reply: str) -> bool | None:
    if _YES.search(reply):
        return True
    if _NO.search(reply):
        return False
    return None


def apply_answer(need: Needs, reply: str) -> dict[str, object]:
    """The facts a reply establishes for the rule that asked, or {} if it establishes
    none."""
    t = reply.strip().lower()
    if not t:
        return {}
    if need.fact == "cpt_full_time_months":
        nums = sorted(_said_numbers(t))
        if re.search(r"\b(a|one|1) (full |whole )?year\b", t):
            nums = [12.0]
        if re.fullmatch(r"(none|zero|no(ne)?)\.?", t):
            nums = [0.0]
        return {"cpt_full_time_months": nums[0]} if len(nums) == 1 else {}
    if need.fact == "eb_category":
        cat = category_of(reply)
        return {"eb_category": cat} if cat else {}
    if need.fact == "country_of_birth":
        return {"country_of_birth": reply.strip()} if area_of(reply) else {}
    if need.fact == "priority_date":
        d = date_of(reply)
        return {"priority_date": d.isoformat()} if d else {}
    if need.fact == "unemployment_days":
        nums = sorted(_said_numbers(t))
        return {"unemployment_days": nums} if nums else {}
    yn = _yes_no(t)
    if yn is None:
        return {}
    if need.fact == "stem_extension":
        return {"stem_extension": yn}
    if need.fact == "cos_pending":
        return {"says_pending": yn, "says_decided": not yn}
    if need.fact == "stem_timely":
        return {"says_timely": yn}
    if need.fact == "stem_decided":
        return {"says_decided": yn, "says_pending": not yn}
    return {}
