"""Citations-first answers: every quote the person sees is real source text.

A claim-level audit found about one unsupported statement per answer even from the best
small model, and no checker a 4b can run catches them (STATE.md findings 28, 30). What a
checker cannot do, a string match can: the model is asked for its answer as JSON — a
direct answer, and for each point a quote copied from a numbered source plus how it
applies — and code decides which quotes are real.

Measured on nine held-out questions before this was built: 21/29 quotes were verbatim in
the source cited, and every one of the other 8 was real text broken mechanically —
several sentences joined into one quote, the right sentence cited to the wrong number, or
the corpus's own HTML entities ("petitioner&nbsp;may"). So a quote is split into
sentences, each sentence is looked for in the cited source and then in the others, the
point is re-attributed to where the text actually is, and what is shown is the source's
own sentence. A sentence found nowhere is dropped, and the drop is reported.

The model's `answer` and `applies` text is still the model's: the renderer labels it so.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "points": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "integer"},
                    "quote": {"type": "string"},
                    "applies": {"type": "string"},
                },
                "required": ["source", "quote", "applies"],
            },
        },
        "ask": {"type": "string"},
    },
    "required": ["answer", "points", "ask"],
}

INSTRUCTION = (
    "Reply as JSON. 'answer': the direct answer for this person, 1-3 sentences, "
    "consistent with any RULE FINDINGS. 'points': up to 4 items, each with 'source' (the "
    "number of a SOURCE), 'quote' (one sentence copied exactly, word for word, from that "
    "source) and 'applies' (how that sentence applies to this person). 'ask': the one "
    "fact that would most change the answer, or an empty string if nothing material is "
    "missing."
)

MIN_QUOTE_CHARS = 25  # shorter fragments match by accident


@dataclass
class Point:
    source: int  # where the text actually is (1-based), after re-attribution
    quote: str  # the source's own text
    applies: str  # the model's application — labelled as such
    cited: int  # what the model said
    moved: bool = False  # re-attributed to a different source


@dataclass
class Checked:
    answer: str
    points: list[Point]
    ask: str
    dropped: list[str] = field(default_factory=list)  # quotes found in no source


_FOLD = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})


def _clean(t: str) -> str:
    """Entities decoded, whitespace collapsed: the text as a reader sees it."""
    return re.sub(r"\s+", " ", html.unescape(t).replace("\u00a0", " ")).strip()


def _fold(t: str) -> str:
    """Case and quote marks folded one character for one, so a match in the folded text
    is at the same offsets in the clean text it came from."""
    return t.translate(_FOLD).lower()


def _sentences(t: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.;:])\s+(?=[A-Z(\"'])", t.strip()) if s.strip()]


def _find(sentence: str, text: str) -> str | None:
    """The source's own wording of `sentence` if `text` contains it, else None."""
    want = _fold(_clean(sentence)).strip(" .;:")
    if len(want) < MIN_QUOTE_CHARS:
        return None
    clean = _clean(text)
    at = _fold(clean).find(want)
    return clean[at : at + len(want)] if at >= 0 else None


def check(raw: dict[str, object], texts: list[str]) -> Checked:
    """Keep the points whose quotes are real, showing the sources' own words."""
    points: list[Point] = []
    dropped: list[str] = []
    for p in list(raw.get("points") or []):  # type: ignore[call-overload]
        if not isinstance(p, dict):
            continue
        cited = int(p.get("source") or 0)
        applies = str(p.get("applies") or "")
        quote = str(p.get("quote") or "")
        for s in _sentences(quote) or [quote]:
            hit: tuple[int, str] | None = None
            # The cited source first, so text found in two sources stays where it was put.
            if 1 <= cited <= len(texts) and (w := _find(s, texts[cited - 1])):
                hit = (cited, w)
            else:
                hit = next(
                    ((i, w) for i, t in enumerate(texts, 1) if (w := _find(s, t))), None
                )
            if hit is None:
                dropped.append(s)
                continue
            points.append(Point(hit[0], hit[1], applies, cited, moved=hit[0] != cited))
            applies = ""  # the application belongs to the point once, not each sentence
    return Checked(
        answer=str(raw.get("answer") or "").strip(),
        points=points,
        ask=str(raw.get("ask") or "").strip(),
        dropped=dropped,
    )


def _echoes(applies: str, quote: str) -> bool:
    """An "application" that only repeats the quote adds nothing — a third of them did."""
    a, q = _fold(_clean(applies)), _fold(_clean(quote))
    return q[:60] in a or a[:60] in q


def render(c: Checked, citation_of: list[str]) -> str:
    """Plain text: the model's answer and applications labelled, the law verbatim."""
    lines = [c.answer]
    if c.points:
        lines.append("")
        lines.append("What the sources say (verified word for word):")
        for p in c.points:
            lines.append(f'  [{p.source}] {citation_of[p.source - 1]}: "{p.quote}"')
            if p.applies and not _echoes(p.applies, p.quote):
                lines.append(f"      how it applies (the tool's reading): {p.applies}")
    if c.ask:
        lines.append("")
        lines.append(f"One thing that would change this: {c.ask}")
    return "\n".join(lines)
