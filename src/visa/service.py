"""One question in, one structured result out — for the desktop window (and any other
front end). The same pipeline the CLI runs, without the printing: retrieval, the rule
table, follow-up questions, the cited answer and every check, returned as plain data.

Follow-up questions work without a terminal: a call returns the questions a rule needs
answered (`needs`) instead of guessing; the window asks the person and calls again with
`replies` ({fact: what they typed}). Facts about the person are saved to the profile, as
in the CLI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import answer as ans
from . import cited, dates, profile, register, rules
from .search import Index


@dataclass
class Source:
    n: int
    citation: str
    quote: str
    applies: str = ""
    moved: bool = False


@dataclass
class Finding:
    decision: str
    because: str
    law: list[dict[str, str]]  # [{citation, quote}]


@dataclass
class Notice:
    headline: str
    instead: str
    still_in_force: str
    checked: str
    stale: bool
    url: str


@dataclass
class Result:
    question: str
    answered: bool  # False when a rule still needs a fact, or the gate declined
    needs: list[dict[str, str]] = field(default_factory=list)  # [{fact, question}]
    declined: str = ""  # the gate's refusal, when no source matched well enough
    warnings: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    answer: str = ""
    sources: list[Source] = field(default_factory=list)
    follow_up: str = ""
    deadlines: str = ""
    notices: list[Notice] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def ask(
    question: str,
    index: Index,
    replies: dict[str, str] | None = None,
    turns: list[ans.Turn] | None = None,
) -> Result:
    """Answer `question`, or return the facts a rule needs first."""
    prof = profile.load()
    pending = dict(replies or {})

    def reply_for(need: rules.Needs) -> str:
        return pending.pop(need.fact, "")

    decided = ans.resolve(
        question, prof, ask=reply_for if pending else None, save=profile.set_value
    )
    needs = [r for r in decided if isinstance(r, rules.Needs)]
    if needs and not replies:
        return Result(
            question,
            answered=False,
            needs=[{"fact": n.fact, "question": n.question} for n in needs],
        )

    hits, gated, warnings = ans.answer(question, index, turns=turns)
    out = Result(question, answered=gated, warnings=warnings)
    out.findings = [
        Finding(
            o.decision,
            o.because,
            [{"citation": s.citation, "quote": s.quote} for s in o.sources],
        )
        for o in decided
        if isinstance(o, rules.Outcome)
    ]
    out.needs = [{"fact": n.fact, "question": n.question} for n in needs]
    out.deadlines = dates.render(profile.load())
    if not gated:
        out.declined = ans.GATE_REFUSAL
        return out

    msgs = ans.build_prompt(question, hits, turns=turns, decided=decided)
    got = ans.cited_answer(msgs, hits, decided)
    labels = ans.cited_labels(hits, decided)
    out.answer = got.answer
    out.follow_up = got.ask
    out.sources = [
        Source(
            p.source,
            labels[p.source - 1],
            p.quote,
            "" if cited._echoes(p.applies, p.quote) else p.applies,
            p.moved,
        )
        for p in got.points
    ]
    for e in register.affecting(
        [h.row.citation for h in hits], texts=[h.row.text for h in hits]
    ):
        out.notices.append(
            Notice(
                e.headline(),
                " ".join(e.instead.split()),
                " ".join(e.not_covered.split()),
                str(e.checked),
                e.is_stale,
                e.source_url,
            )
        )
    text = "\n".join([got.answer, *(p.applies for p in got.points)])
    out.problems = (
        ans.verify_dates(
            text, hits=hits, settled=f"{rules.render(decided, head='')}\n{question}"
        )
        + rules.contradictions(text, decided, question)
        + (
            [f"{len(got.dropped)} quoted sentence(s) found in no source, not shown"]
            if got.dropped
            else []
        )
    )
    return out
