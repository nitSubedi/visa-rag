"""Prompt assembly, the citation gate, and streaming generation."""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from collections.abc import Iterator

from . import dates, profile
from .config import settings, tier_label
from .search import Hit, Index, passes_gate

SYSTEM = """You are a research assistant for U.S. immigration questions. You are not a \
lawyer and you do not give legal advice.

ABSOLUTE RULES:
1. Answer ONLY from the numbered SOURCES below. If they do not support an answer, say
   plainly: "The sources I have don't cover this." Never fill a gap from memory.
2. Cite every substantive claim inline as [1], [2] matching the source numbers above.
   Cite ONLY by bracketed number. Never invent a section number, and never write a
   citation like "[8 CFR 204.5(d)]" — if you name a provision, it must appear verbatim
   in a source, and the bracket must contain only the digit.
3. Quote the operative language verbatim when a specific standard or deadline matters.
4. Respect legal precedence: statute (8 U.S.C.) outranks regulation (8 CFR), which
   outranks USCIS Policy Manual, which outranks guidance. When a Policy Manual passage
   supplies a test that the regulation does not, say so explicitly — it reflects how
   USCIS adjudicates, not binding law.
5. If COMPUTED DEADLINES are provided, use those dates exactly. Do not do your own date
   arithmetic and do not restate a date that contradicts them.
6. Never predict whether a petition will be approved. You may map evidence to criteria
   and identify what is thin, but adjudication outcomes are not yours to forecast.
7. Be concrete and brief. No preamble, no restating the question."""

CLOSER = (
    "This is research, not legal advice. For anything that affects your status, "
    "confirm with your DSO (free, and they control your SEVIS record) or an "
    "immigration attorney."
)


def build_prompt(
    question: str,
    hits: list[Hit],
    prof: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    prof = prof if prof is not None else profile.load()
    blocks = []
    for i, h in enumerate(hits, 1):
        r = h.row
        blocks.append(
            f"[{i}] {r.citation}  ({tier_label(r.tier)} · {r.source_title})\n{r.text}"
        )
    parts: list[str] = []
    p = profile.render(prof)
    if p:
        parts.append(p)
    d = dates.render(prof)
    if d:
        parts.append(d)
    parts.append("SOURCES:\n\n" + "\n\n".join(blocks))
    parts.append(f"QUESTION: {question}")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def stream_chat(
    messages: list[dict[str, str]], model: str | None = None, temperature: float = 0.15
) -> Iterator[str]:
    payload: dict[str, object] = {
        "model": model or settings.chat_model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature, "num_ctx": 8192},
    }
    req = urllib.request.Request(
        f"{settings.ollama_host}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("error"):
                raise RuntimeError(d["error"])
            tok = (d.get("message") or {}).get("content", "")
            if tok:
                yield tok
            if d.get("done"):
                break


# Priority dates move monthly; a stale snapshot answering them confidently is the one
# genuinely dangerous failure mode of an offline tool.
PRIORITY_DATE_TERMS = (
    "priority date",
    "visa bulletin",
    "final action date",
    "retrogress",
    "current for",
    "dates for filing",
    "backlog",
)


def needs_live_bulletin(question: str) -> bool:
    q = question.lower()
    return any(t in q for t in PRIORITY_DATE_TERMS)


def answer(
    question: str, index: Index, k: int | None = None
) -> tuple[list[Hit], bool, list[str]]:
    """Returns (hits, gate_passed, preamble_warnings)."""
    warnings = list(index.warnings)
    if needs_live_bulletin(question):
        warnings.append(
            "Priority-date questions depend on the monthly Visa Bulletin, which this "
            "tool deliberately does not cache. Check travel.state.gov for the current "
            "month — any cached answer would risk being wrong."
        )
    hits = index.search(question, k=k)
    return hits, passes_gate(hits), warnings


CITE_RE = re.compile(r"\b(\d{1,2})\s*(?:CFR|C\.F\.R\.)\s*§?\s*([\d.]+[a-z0-9()]*)", re.I)
USC_RE = re.compile(r"\b(\d{1,2})\s*U\.?S\.?C\.?\s*§?\s*([\d]+[a-z0-9()\-]*)", re.I)
BRACKET_RE = re.compile(r"\[(\d{1,2})\]")


def verify_citations(text: str, hits: list[Hit]) -> list[str]:
    """Catch fabricated citations before the user acts on one.

    The model has been observed emitting things like "[6 CFR § 204.5(d)]" — welding a
    source number onto an invented provision. A wrong-looking citation is worse than a
    vague answer, because it is checkable-looking and therefore trusted.
    """
    problems = []
    corpus = " ".join(h.row.text + " " + h.row.citation for h in hits).lower()

    for n in {int(m) for m in BRACKET_RE.findall(text)}:
        if not 1 <= n <= len(hits):
            problems.append(f"cited [{n}] but only {len(hits)} sources were provided")

    for rx, label, valid_titles in ((CITE_RE, "CFR", {"8"}), (USC_RE, "U.S.C.", {"8"})):
        for title, sec in rx.findall(text):
            if title not in valid_titles:
                problems.append(
                    f"cited '{title} {label} {sec}' — this corpus only contains title "
                    f"{'/'.join(sorted(valid_titles))}, so that citation is wrong"
                )
                continue
            if sec.split("(")[0].lower() not in corpus:
                problems.append(
                    f"cited '{title} {label} {sec}' but no retrieved source contains it"
                )
    return problems


def sources_table(hits: list[Hit]) -> list[tuple[int, str, str, str]]:
    out = []
    for i, h in enumerate(hits, 1):
        r = h.row
        out.append((i, r.citation, tier_label(r.tier), f"{h.cosine:.2f}"))
    return out


def today() -> str:
    return dt.date.today().isoformat()
