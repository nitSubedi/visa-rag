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
}


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
    said = {float(n) for n in re.findall(r"\b\d+(?:\.\d+)?\b", t)}
    said |= {float(n) for w, n in _WORDS.items() if re.search(rf"\b{w}\b", t)}
    if re.search(r"\b(a|one|1) (full |whole )?year\b", t):
        said.add(12.0)
    return v in said


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


RULES: tuple[Rule, ...] = (
    Rule("cpt_full_time_year", _cpt_applies, _cpt_decide, CPT_SOURCES),
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
