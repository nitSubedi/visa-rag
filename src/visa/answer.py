"""Prompt assembly, the citation gate, and streaming generation."""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from . import dates, profile
from .config import settings, tier_label
from .search import Hit, Index, passes_gate

SYSTEM = """You are a research assistant for U.S. immigration questions. You are not a \
lawyer and you do not give legal advice.

WHERE THE LAW COMES FROM — these are absolute:
1. Take every statement of law ONLY from the numbered SOURCES below. If they do not
   support an answer, say plainly: "The sources I have don't cover this." Never fill a
   legal gap from memory.
2. Cite every substantive claim inline as [1], [2] matching the source numbers above.
   Cite ONLY by bracketed number. Never invent a section number, and never write a
   citation like "[8 CFR 204.5(d)]" — if you name a provision, it must appear verbatim
   in a source, and the bracket must contain only the digit.
3. Quote the operative language verbatim when a specific standard or deadline matters.
4. Respect legal precedence: statute (8 U.S.C.) outranks regulation (8 CFR), which
   outranks USCIS Policy Manual, which outranks guidance. When a Policy Manual passage
   supplies a test that the regulation does not, say so explicitly — it reflects how
   USCIS adjudicates, not binding law.
5. COMPUTED DEADLINES are authoritative and already correct. Quote those dates exactly,
   never recompute them, and address EVERY line of that block that bears on the
   question — including any that show a limit already partly consumed or a window
   already closed. Silently omitting one is a failure.
6. Never predict whether a petition will be approved. You may map evidence to criteria
   and identify what is thin, but adjudication outcomes are not yours to forecast.

WHAT TO DO WITH IT — rule 1 governs where law comes from. It does not excuse you from
thinking. Reasoning about this person is required, not optional:
7. The USER PROFILE and the question describe one specific person. Work out which of
   the sources actually govern THEIR situation, and say so. An answer that is true in
   general but never connects to their circumstances has failed, however well cited.
   But use ONLY the facts they actually gave you. Never assert a fact about them that
   is not in the profile or their own words — not their field of study, not their
   dates, not their current status. Inventing a fact about someone's immigration
   position is worse than any generic answer.
8. Lead with what their own facts already settle, and name the provision that decides
   the case — not every provision the search happened to return. If a source imposes a
   condition they plainly do not meet, that is the answer; say it first.
9. Where a rule turns on a fact you were not given, name that fact and say what turns
   on it. Do not enumerate every branch — mark the conclusion as contingent on that one
   thing and move on.
10. Close with at most ONE question: the single fact that would most change your answer.
    If nothing material is missing, ask nothing. Never ask instead of answering — you
    answer first, with whatever you have.
11. Be concrete. No preamble, no restating the question. Length should come from the
    reasoning, never from padding."""

CLOSER = (
    "This is research, not legal advice. For anything that affects your status, "
    "confirm with your DSO (free, and they control your SEVIS record) or an "
    "immigration attorney."
)


def est_tokens(text: str) -> int:
    return int(len(text.split()) * 1.33)


@dataclass
class Turn:
    """One exchange. The answer is kept only so the next question can build on it."""

    question: str
    answer: str


def condense(answer_text: str, max_tokens: int) -> str:
    """Keep the front of an answer, which rule 8 puts the conclusion in.

    Carrying full prose forward would spend the window that sources need. Sentences
    are kept whole — a conclusion truncated mid-clause can invert its meaning, which
    is the last thing to feed back into a legal prompt.
    """
    out: list[str] = []
    used = 0
    for sent in re.split(r"(?<=[.!?])\s+", " ".join(answer_text.split())):
        cost = est_tokens(sent)
        if used + cost > max_tokens and out:
            break
        out.append(sent)
        used += cost
    return " ".join(out)


def render_history(turns: list[Turn], budget: int | None = None) -> str:
    """The recent exchange, newest last, hard-capped."""
    budget = settings.history_tokens if budget is None else budget
    recent = turns[-settings.history_turns :]
    if not recent or budget <= 0:
        return ""
    per = max(24, budget // (2 * max(1, len(recent))))
    blocks = [
        f"  they asked: {t.question}\n  you answered: {condense(t.answer, per)}"
        for t in recent
    ]
    body = "\n".join(blocks)
    while est_tokens(body) > budget and len(blocks) > 1:
        blocks.pop(0)
        body = "\n".join(blocks)
    return f"EARLIER IN THIS CONVERSATION (oldest first):\n{body}"


def retrieval_query(question: str, turns: list[Turn] | None = None) -> str:
    """What to search for.

    A follow-up is often unintelligible alone — "what if it isn't E-Verify
    enrolled?" embeds toward nothing useful. Prepend the previous question so the
    search sees the subject the pronoun refers to.
    """
    if not turns:
        return question
    return f"{turns[-1].question} {question}"


def build_prompt(
    question: str,
    hits: list[Hit],
    prof: dict[str, object] | None = None,
    turns: list[Turn] | None = None,
) -> list[dict[str, str]]:
    """Assemble the prompt, ordered and budgeted so nothing critical is truncated.

    Two things learned the hard way:

    * The prompt must fit. Twelve chunks of legal text ran ~8.3k tokens against an
      8k window, so generation silently dropped whatever came first.
    * Order matters. Profile and computed deadlines go LAST, immediately before the
      question — those are the facts an answer must not contradict, and the tail of
      a prompt is both un-truncated and the best attended.
    """
    prof = prof if prof is not None else profile.load()

    facts: list[str] = []
    if p := profile.render(prof):
        facts.append(p)
    if d := dates.render(prof):
        facts.append(d)
    # History sits above the facts, never below: profile and deadlines must stay
    # closest to the question, and history is the part that may be dropped.
    parts = [render_history(turns or []), *facts, f"QUESTION: {question}"]
    tail = "\n\n".join(x for x in parts if x)

    budget = (
        settings.num_ctx
        - settings.answer_reserve_tokens
        - est_tokens(SYSTEM)
        - est_tokens(tail)
    )

    blocks: list[str] = []
    used = 0
    for i, h in enumerate(hits, 1):
        r = h.row
        block = f"[{i}] {r.citation}  ({tier_label(r.tier)} · {r.source_title})\n{r.text}"
        cost = est_tokens(block)
        if used + cost > budget and blocks:
            break
        blocks.append(block)
        used += cost

    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "SOURCES:\n\n" + "\n\n".join(blocks) + "\n\n" + tail},
    ]


def stream_chat(
    messages: list[dict[str, str]], model: str | None = None, temperature: float = 0.15
) -> Iterator[str]:
    payload: dict[str, object] = {
        "model": model or settings.chat_model,
        "messages": messages,
        "stream": True,
        "keep_alive": settings.keep_alive,
        "options": {"temperature": temperature, "num_ctx": settings.num_ctx},
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


# A bare lookup ("what is X") needs one retrieval. A described situation needs one per
# legal issue, because the deciding provision is usually not the one named.
SITUATIONAL_HINTS = ("i ", "my ", "me ", "i'm", "i've", "we ", "our ")


def looks_situational(question: str) -> bool:
    q = question.lower()
    return len(q.split()) > 12 and any(h in f" {q} " for h in SITUATIONAL_HINTS)


def answer(
    question: str,
    index: Index,
    k: int | None = None,
    on_issue: Callable[[list[str]], None] = lambda _: None,
    turns: list[Turn] | None = None,
) -> tuple[list[Hit], bool, list[str]]:
    """Returns (hits, gate_passed, preamble_warnings)."""
    warnings = list(index.warnings)
    if needs_live_bulletin(question):
        warnings.append(
            "Priority-date questions depend on the monthly Visa Bulletin, which this "
            "tool deliberately does not cache. Check travel.state.gov for the current "
            "month — any cached answer would risk being wrong."
        )

    query = retrieval_query(question, turns)
    if settings.retrieval == "issues" and looks_situational(query):
        issues = plan_issues(query)
        if issues:
            on_issue(issues)
            merged = index.search_many([query, *issues], k=k)
            # The gate still applies: decomposition must not become a way for an
            # off-domain question to sneak past the relevance floor.
            return merged, passes_gate(merged), warnings

    hits = index.search(query, k=k)
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


ISO_DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def trailing_questions(text: str) -> list[str]:
    """The question sentences an answer closes with.

    Rule 10 allows exactly one — the single fact that would most change the answer.
    A list of questions is the same enumerating behaviour in a different costume, and
    it hands the work back to someone who came here precisely because they did not
    know which question to ask.
    """
    paras = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    if not paras:
        return []
    return [q.strip() for q in re.findall(r"[^.!?\n]*\?", paras[-1]) if q.strip()]


def verify_dialogue(text: str) -> list[str]:
    """Answer first, then ask at most one thing."""
    qs = trailing_questions(text)
    if len(qs) > 1:
        return [
            f"closed with {len(qs)} questions — ask only the one that would most "
            f"change the answer"
        ]
    return []


# Sentences that tell someone what they must, may, or cannot do. Deliberately
# over-inclusive: a false flag costs a glance at the panel, a missed one costs
# somebody a wrong belief about their immigration status.
DEONTIC_RE = re.compile(
    r"\b(?:must|cannot|can't|may not|is barred|are barred|is required|are required|"
    r"is eligible|are eligible|not eligible|qualifies|does not qualify|you can|"
    r"you may|you should|you need to|you have to|you would need)\b",
    re.I,
)


def verify_grounding(text: str) -> list[str]:
    """Every claim about what this person must or may do has to hang off a source.

    `verify_citations` validates the brackets that are present; it says nothing about
    an answer that cites nothing at all. Once the model is asked to reason rather than
    recite, that gap becomes the whole problem — observed live: fifteen legal claims,
    zero citations, every existing check green.

    This does not catch bad inference. It makes unsupported assertion visible, which
    is the achievable goal.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n|\n(?=\s*[-*\u2022]|\s*\d+\.)", text)]
    ungrounded = [
        b
        for b in blocks
        if b and DEONTIC_RE.search(b) and not re.search(r"\[\d{1,2}\]", b)
    ]
    if not ungrounded:
        return []
    return [
        f"{len(ungrounded)} passage(s) state what you must or may do without citing a "
        f"source — first: \"{' '.join(ungrounded[0].split())[:90]}…\""
    ]


def verify_dates(text: str, prof: dict[str, object] | None = None) -> list[str]:
    """Catch the model re-deriving a deadline and drifting off by a day or two.

    Observed: an answer correctly quoted the filing window opening 2027-02-13, then
    concluded "the earliest filing date would be 2027-02-14". A deadline that is
    almost right is the most dangerous output this tool can produce, so any date
    close to — but not equal to — a computed boundary is reported.
    """
    windows = dates.compute(prof if prof is not None else profile.load())
    if not windows:
        return []
    boundaries = {
        d: w.name for w in windows for d in (w.opens, w.closes) if d is not None
    }
    if not boundaries:
        return []

    problems = []
    for raw in set(ISO_DATE_RE.findall(text)):
        try:
            got = dt.date.fromisoformat(raw)
        except ValueError:
            continue
        if got in boundaries:
            continue
        for boundary, name in boundaries.items():
            delta = abs((got - boundary).days)
            if 0 < delta <= 3:
                problems.append(
                    f"answer says {raw}, but the computed {name} boundary is "
                    f"{boundary.isoformat()} ({delta}d off) — trust the computed date"
                )
                break
    return problems


def plan_issues(question: str, prof: dict[str, object] | None = None) -> list[str]:
    """Decompose a described situation into the legal issues it raises.

    A single retrieval embeds the user's narrative, which surfaces passages similar to
    what they *said* — not the provisions that actually decide the case. Issue-spotting
    is the part a person cannot do for themselves: the rule that disqualifies you is
    the one you did not know to ask about.
    """
    prof = prof if prof is not None else profile.load()
    facts = profile.render(prof)
    prompt = (
        "You are triaging a U.S. immigration question for a legal research index.\n"
        "List the distinct legal issues these facts raise — including issues the "
        "person did NOT ask about but that bear on the outcome (eligibility limits, "
        "accrued time, employer requirements, filing windows, status violations).\n"
        "Output 3-6 lines. Each line is a short search query in legal terminology, "
        "no numbering, no commentary.\n\n"
        f"{facts}\n\nSITUATION: {question}"
    )
    try:
        raw = "".join(
            stream_chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
            )
        )
    except Exception:
        return []
    issues = []
    for line in raw.splitlines():
        cleaned = re.sub(r"^\s*[-*\d.)\s]+", "", line).strip()
        if 8 < len(cleaned) < 140:
            issues.append(cleaned)
    return issues[:6]


def sources_table(hits: list[Hit]) -> list[tuple[int, str, str, str]]:
    out = []
    for i, h in enumerate(hits, 1):
        r = h.row
        out.append((i, r.citation, tier_label(r.tier), f"{h.cosine:.2f}"))
    return out


def today() -> str:
    return dt.date.today().isoformat()
