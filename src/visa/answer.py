"""Prompt assembly, the citation gate, and streaming generation."""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from . import dates, profile, register, rules, terminology
from .cites import CFR_SECTION
from .config import settings, tier_label
from .search import Hit, Index, passes_gate

SYSTEM = """You are a research assistant for U.S. immigration questions. You are not a \
lawyer and you do not give legal advice.

The USER PROFILE and question describe one specific person. Work out which sources
actually govern THEIR situation. An answer true in general but never connected to
their circumstances has failed. Lead with what their facts settle and name the
provision that decides the case, not every provision the search returned. Where a
rule turns on a fact you were not given, name that fact rather than enumerating
branches. No preamble. Never predict whether a petition will be approved.

COMPUTED DEADLINES are authoritative and already correct: quote those dates exactly,
never recompute them, and address every line bearing on the question. Respect
precedence: statute (8 U.S.C.) outranks regulation (8 CFR), outranks USCIS Policy
Manual, outranks guidance. Where the Policy Manual supplies a test the regulation
does not, say so.

End with at most ONE question — the single fact that would most change your answer.
After answering, never instead of it. If nothing material is missing, ask nothing.

THESE TWO RULES OVERRIDE EVERYTHING ABOVE:
1. Every statement of law comes ONLY from the numbered SOURCES. Where they leave a
   point unsettled, name that point and say the sources do not settle it. Never fill
   a legal gap from memory, and never invent a requirement, form, or regulatory
   category.
2. EVERY substantive claim carries an inline [n] matching a source number. A sentence
   telling this person what they must, may, or cannot do WITHOUT a bracket is a
   failure. Put the source number in brackets after the claim, like [3]; never write
   "[8 CFR 204.5(d)]"."""

# What the user sees when the citation gate declines. One string, so the eval scores
# exactly what the CLI prints.
GATE_REFUSAL = "The sources I have don't cover this well enough to answer."

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
    """What the person has already told you, oldest first.

    Feeding back the previous *answer* made a 7b replay it: the second reply in a
    live session repeated three whole sections of the first verbatim and appended a
    single new line. Their own words are the part worth carrying — those are the
    facts of the case, and they cannot be echoed into a non-answer.
    """
    budget = settings.history_tokens if budget is None else budget
    recent = turns[-settings.history_turns :]
    if not recent or budget <= 0:
        return ""
    blocks = [f"  - {t.question}" for t in recent]
    body = "\n".join(blocks)
    while est_tokens(body) > budget and len(blocks) > 1:
        blocks.pop(0)
        body = "\n".join(blocks)
    return (
        "WHAT THEY HAVE ALREADY TOLD YOU THIS CONVERSATION (oldest first). Treat "
        "these as established facts about them, and do not repeat an earlier answer "
        "back to them — build on it:\n" + body
    )


# A question that cannot stand on its own: too short to embed usefully, or opening
# with something that points back at what was just said.
ANAPHORIC_OPENERS = (
    "what if", "and ", "but ", "so ", "then ", "what about", "how about",
    "why ", "no ", "yes ", "it ", "that ", "they ", "he ", "she ", "ok", "okay",
)


def needs_context(question: str) -> bool:
    q = question.strip().lower()
    return len(q.split()) <= 8 or q.startswith(ANAPHORIC_OPENERS)


def retrieval_query(question: str, turns: list[Turn] | None = None) -> str:
    """What to search for.

    A follow-up is often unintelligible alone — "what if it isn't E-Verify
    enrolled?" embeds toward nothing useful. Prepend the previous question so the
    search sees the subject the pronoun refers to.

    Only when it is actually needed. Prepending unconditionally hijacked a
    self-contained question: asked what the other STEM OPT requirements were, with
    "no i have not filed h1b at all" glued on front, retrieval returned three H-1B
    chunks and zero SEVP ones, and the answer followed the sources off-topic.
    """
    if not turns or not needs_context(question):
        return question
    return f"{turns[-1].question} {question}"


def build_prompt(
    question: str,
    hits: list[Hit],
    prof: dict[str, object] | None = None,
    turns: list[Turn] | None = None,
    decided: list[rules.Outcome | rules.Needs] | None = None,
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
    if decided is None:
        decided = decide(question, prof)
    if found := rules.render(decided):
        facts.append(found)
    # A provision can be in the corpus and suspended by a court. This does not compete
    # with the passages — it overrides them — so it goes in the tail with the facts,
    # where it is un-truncatable and best attended, never in SOURCES.
    affected = register.affecting(
        [h.row.citation for h in hits], texts=[h.row.text for h in hits]
    )
    withheld = [
        f"[{i}] {h.row.citation}"
        for i, h in enumerate(hits, 1)
        if register.suspended(h.row.citation, h.row.text)
        or register.redact(h.row.citation, h.row.text)[1]
    ]
    if n := register.note_for_prompt(affected, withheld):
        facts.append(n)
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

    reg = register.load()
    # Not `blocks` — that is the module-level passage splitter shared by uncited_claims
    # and verify_dates, and shadowing it here would be a trap for the next edit.
    source_blocks: list[str] = []
    used = 0
    for i, h in enumerate(hits, 1):
        r = h.row
        head = f"[{i}] {r.citation}  ({tier_label(r.tier)} · {r.source_title})"
        # Withhold the text of a suspended passage rather than annotate it. Finding 19:
        # a 7b handed the enjoined rule plus a warning quoted the rule, invented a filing
        # date from it, and ignored the correct computed dates sitting in the tail — 228
        # tokens of caution lost to ~8,000 tokens of on-topic passage. The slot stays so
        # `/sources N` and verify_citations keep their numbering, and the passage is still
        # printed in the sources panel; the model simply has nothing to quote.
        text, _ = register.redact(r.citation, r.text, reg)
        if e := register.suspended(r.citation, r.text, reg):
            block = (
                f"{head}\n[SUSPENDED — withheld] A court has suspended this provision "
                f"({e.case}, {e.docket}, {e.order_date}). Its text is deliberately not "
                f"shown. Do not state it as law. Use [n] for other sources."
            )
        else:
            block = f"{head}\n{text}"
        cost = est_tokens(block)
        if used + cost > budget and source_blocks:
            break
        source_blocks.append(block)
        used += cost

    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": "SOURCES:\n\n" + "\n\n".join(source_blocks) + "\n\n" + tail,
        },
    ]


_SYSTEM_ROLE: dict[str, bool] = {}


def has_system_role(model: str) -> bool:
    """Gemma's instruction-tuned models have only `user` and `model` roles; Google's
    guidance is to put system-level instructions in the first user turn
    (ai.google.dev/gemma/docs/core/prompt-structure). Ollama's gemma3 template instead
    renders a system message as a user turn of its own, so the model saw our rules as
    a separate message from someone, followed by a second one holding the question.
    Read from the model's own metadata; unknown means leave the messages alone."""
    if model not in _SYSTEM_ROLE:
        try:
            req = urllib.request.Request(
                f"{settings.ollama_host}/api/show",
                data=json.dumps({"model": model}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                details = json.load(r).get("details") or {}
            families = [details.get("family") or "", *(details.get("families") or [])]
            _SYSTEM_ROLE[model] = not any(f.startswith("gemma") for f in families)
        except Exception:
            return True
    return _SYSTEM_ROLE[model]


def fold_system(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Move system text to the start of the next user turn, as the model expects."""
    out: list[dict[str, str]] = []
    pending: list[str] = []
    for m in messages:
        if m["role"] == "system":
            pending.append(m["content"])
        elif m["role"] == "user" and pending:
            out.append({"role": "user", "content": "\n\n".join([*pending, m["content"]])})
            pending = []
        else:
            out.append(m)
    if pending:
        out.append({"role": "user", "content": "\n\n".join(pending)})
    return out


def stream_chat(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.15,
    fmt: dict[str, object] | None = None,
    max_tokens: int | None = None,
) -> Iterator[str]:
    """`fmt` is a JSON schema. Ollama masks tokens that would violate it during
    sampling, so an out-of-schema reply is unreachable rather than discouraged — the
    one thing prompt wording has repeatedly failed to achieve on a 7b."""
    model = model or settings.chat_model
    if not has_system_role(model):
        messages = fold_system(messages)
    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": settings.keep_alive,
        "options": {
            "temperature": temperature,
            "num_ctx": settings.num_ctx,
            # Always capped. Without num_predict a model that never emits an end token
            # generates until the context fills — a hang, not an answer.
            "num_predict": max_tokens or settings.answer_reserve_tokens,
        },
    }
    if fmt is not None:
        payload["format"] = fmt
    if settings.think is not None:
        payload["think"] = settings.think
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
    # Search in the law's own words as well as the user's: "EB-1A" and "CPT" appear
    # nowhere in the regulations that govern them. Retrieval only — the prompt is not
    # touched. See terminology.py.
    exclude = terminology.excluded(query)
    if settings.retrieval == "issues" and looks_situational(query):
        issues = plan_issues(query)
        if issues:
            on_issue(issues)
            merged = index.search_many(
                [terminology.expand(q) for q in (query, *issues)], k=k, exclude=exclude
            )
            # The gate still applies: decomposition must not become a way for an
            # off-domain question to sneak past the relevance floor.
            return merged, passes_gate(merged), warnings

    hits = index.search(terminology.expand(query), k=k, exclude=exclude)
    return hits, passes_gate(hits), warnings


CITE_RE = re.compile(
    rf"\b(\d{{1,2}})\s*(?:CFR|C\.F\.R\.)\s*§?\s*({CFR_SECTION}[a-z0-9()]*)", re.I
)
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
# Models write deadlines in prose ("October 1, 2027"), which the ISO pattern never
# saw — so a date wrong by a full year passed every check.
MONTHS = {
    m: i
    for i, full in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        1,
    )
    for m in (full, full[:3])
}
PROSE_DATE_RE = re.compile(
    r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")[a-z]*\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b",
    re.I,
)


def _iter_dates(text: str) -> list[tuple[str, dt.date]]:
    """Every date an answer states, ISO or prose."""
    out: list[tuple[str, dt.date]] = []
    for raw in ISO_DATE_RE.findall(text):
        try:
            out.append((raw, dt.date.fromisoformat(raw)))
        except ValueError:
            continue
    for mon, day, year in PROSE_DATE_RE.findall(text):
        try:
            d = dt.date(int(year), MONTHS[mon.lower()[:3]], int(day))
        except (ValueError, KeyError):
            continue
        out.append((f"{mon} {day}, {year}", d))
    return out


def _date_forms(d: dt.date) -> tuple[str, ...]:
    """The spellings a source might use for the same day."""
    full = d.strftime("%B")
    return (
        d.isoformat(),
        f"{full} {d.day}, {d.year}",
        f"{full} {d.day} {d.year}",
        f"{full[:3]} {d.day}, {d.year}",
        f"{d.month}/{d.day}/{d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}",
    )


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


def blocks(text: str) -> list[str]:
    """Paragraphs and list items — the unit a citation is understood to cover.

    Shared so grounding and date support agree on what a passage is. A bracket in one
    paragraph does not vouch for a claim in the next.
    """
    parts = re.split(r"\n\s*\n|\n(?=\s*[-*\u2022]|\s*\d+\.)", text)
    return [b.strip() for b in parts if b.strip()]


def uncited_claims(text: str) -> list[str]:
    """Passages stating what the person must or may do, carrying no bracket.

    Split out from `verify_grounding` because counting them and attributing them need
    the same selection rule. If the two ever disagreed about what a claim is, the
    answer-check panel would report a number it could not explain.
    """
    return [
        b
        for b in blocks(text)
        if DEONTIC_RE.search(b) and not re.search(r"\[\d{1,2}\]", b)
    ]


def verify_grounding(text: str) -> list[str]:
    """Every claim about what this person must or may do has to hang off a source.

    `verify_citations` validates the brackets that are present; it says nothing about
    an answer that cites nothing at all. Once the model is asked to reason rather than
    recite, that gap becomes the whole problem — observed live: fifteen legal claims,
    zero citations, every existing check green.

    This does not catch bad inference. It makes unsupported assertion visible, which
    is the achievable goal.
    """
    ungrounded = uncited_claims(text)
    if not ungrounded:
        return []
    return [
        f"{len(ungrounded)} passage(s) state what you must or may do without citing a "
        f"source — first: \"{' '.join(ungrounded[0].split())[:90]}…\""
    ]


# Asked as a typed question rather than enforced as prose, because "nothing supports
# this" has to be sayable. A grammar mandating a citation per sentence would fix the
# omission count and manufacture false attributions to do it — trading a visible gap
# for an invisible error, which in a legal tool is the worse of the two.
ATTRIBUTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "attributions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "integer"},
                    "source": {"type": ["integer", "null"]},
                },
                "required": ["claim", "source"],
            },
        }
    },
    "required": ["attributions"],
}


def parse_attributions(raw: str, n_claims: int, n_sources: int) -> dict[int, int | None]:
    """Validate a typed attribution reply against the slate that was actually shown.

    The schema constrains shape, never truth: `{"source": 64}` is valid JSON and a
    fabricated citation. Both failures this project has recorded are of exactly that
    kind — a source number welded onto an invented provision, and a Policy Manual
    footnote copied into an answer where it is in range but points elsewhere. So a
    number outside 1..n_sources degrades to "unsupported" rather than to a citation.
    """
    try:
        data = json.loads(raw)
        items = data["attributions"]
        if not isinstance(items, list):
            return {}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}

    out: dict[int, int | None] = {}
    for item in items:
        if not isinstance(item, dict) or "claim" not in item or "source" not in item:
            continue
        claim, src = item["claim"], item["source"]
        if not isinstance(claim, int) or isinstance(claim, bool):
            continue
        if not 0 <= claim < n_claims or claim in out:  # first answer wins
            continue
        ok = isinstance(src, int) and not isinstance(src, bool) and 1 <= src <= n_sources
        out[claim] = src if ok else None
    return out


def attribute_claims(
    claims: list[str], hits: list[Hit], messages: list[dict[str, str]], answer_text: str
) -> dict[int, int | None]:
    """Ask which source supports each uncited claim, or none.

    Appends to `messages` rather than rebuilding the prompt. That is not a style
    choice: the sources are already in the KV cache from generation, so an appended
    turn re-prefills in ~0.2s instead of ~28s. Measured 1.0s total against 41.6s for
    the answer itself — rebuild the prefix and it costs a second full prefill.
    """
    if not claims or not hits:
        return {}
    numbered = "\n".join(f"{i}. {' '.join(c.split())}" for i, c in enumerate(claims))
    ask = (
        "For each numbered claim below, say which source number supports it.\n"
        f"Valid source numbers are 1 to {len(hits)}.\n"
        "Use null when no listed source supports the claim. Do not guess: null is the "
        "correct answer whenever the sources do not state it.\n\n"
        f"CLAIMS:\n{numbered}"
    )
    convo = [
        *messages,
        {"role": "assistant", "content": answer_text},
        {"role": "user", "content": ask},
    ]
    try:
        raw = "".join(
            stream_chat(convo, temperature=0.0, fmt=ATTRIBUTION_SCHEMA)
        )
    except Exception:
        return {}
    return parse_attributions(raw, len(claims), len(hits))


def verify_dates(
    text: str,
    prof: dict[str, object] | None = None,
    hits: list[Hit] | None = None,
) -> list[str]:
    """Catch a stated deadline that the computed facts and sources do not support.

    Two failures, both observed live:

    * Drift. An answer correctly quoted the filing window opening 2027-02-13, then
      concluded "the earliest filing date would be 2027-02-14". Almost right is the
      most dangerous output this tool produces.
    * Derivation. An answer wrote "the H-1B start date of October 1, 2027" for a
      FY2027 cap-gap that runs to an October 1 *2026* start. Wrong by a year, in
      prose the ISO pattern never matched, past every check.

    Pass `hits` to enable the second check: a date belongs in an answer only if it
    appears in the computed deadlines or verbatim in a retrieved source. A date the
    model worked out for itself is flagged even when it happens to be correct —
    deriving deadlines is precisely what the deterministic-dates design forbids, and
    a right answer reached the forbidden way is right by luck.
    """
    prof = prof if prof is not None else profile.load()
    windows = dates.compute(prof)
    boundaries = {
        d: w.name for w in windows for d in (w.opens, w.closes) if d is not None
    }

    computed = dates.render(prof)
    reg = register.load() if hits else []

    def cited_text(block: str) -> str:
        """What may vouch for the dates in this block.

        Where the block cites sources, only those sources count. That closes the
        laundering case — a date carried by [1] cannot be justified by pointing at [2] —
        without widening anything.

        Where the block cites nothing, the whole slate counts, as before. Requiring a
        bracket for every date would make date safety depend on citation compliance,
        which is this project's known-erratic feature: the same conversation produced 7,
        then 0, then 1 citations. Most correct dates are uncited, so the check would fire
        on them constantly — finding 14b's lesson that a warning crying wolf
        systematically is worse than no warning.

        Suspended passages never count either way. They are withheld from the prompt, so
        the model cannot have been quoting them, and counting them let an invented
        2026-12-14 pass on the strength of an enjoined transition provision for a
        different visa class.
        """
        if hits is None:
            return computed
        usable = [
            h for h in hits if not register.suspended(h.row.citation, h.row.text, reg)
        ]
        cited = [
            hits[n - 1]
            for n in sorted({int(x) for x in BRACKET_RE.findall(block)})
            if 1 <= n <= len(hits)
        ]
        scope = [h for h in cited if h in usable] or usable
        kept = [register.redact(h.row.citation, h.row.text, reg)[0] for h in scope]
        return "\n".join([computed, *kept])

    problems = []
    for block in blocks(text) or [text]:
        supported = cited_text(block)
        for raw, got in _iter_dates(block):
            if got in boundaries:
                continue
            # Support first, drift second. A window's close has a neighbour by design —
            # cap-gap ends Sep 30 and the new status begins Oct 1 — so checking drift
            # first flagged a correct October 1 as "1d off", on every cap-gap answer.
            # A warning that cries wolf systematically is worse than none.
            if any(form.lower() in supported.lower() for form in _date_forms(got)):
                continue
            near = next(
                ((b, n) for b, n in boundaries.items() if 0 < abs((got - b).days) <= 3),
                None,
            )
            if near:
                b, name = near
                problems.append(
                    f"answer says {raw}, but the computed {name} boundary is "
                    f"{b.isoformat()} ({abs((got - b).days)}d off) — "
                    f"trust the computed date"
                )
                continue
            if hits is None:
                continue
            problems.append(
                f"answer states {raw}, which no source it cites contains and no "
                f"computed deadline matches — the model worked it out, so check it"
            )
    return _dedupe(problems)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# Which rules a question puts in play. Extraction runs only when one matches.
RULE_TRIGGERS = {
    "mentions_cpt": re.compile(r"\bCPT\b|curricular practical training", re.I),
    "mentions_unemployment": re.compile(r"\bunemploy", re.I),
}
STEM_EXTENSION = re.compile(r"\bSTEM\b[^.?!]{0,30}\bextension|\bSTEM OPT\b", re.I)


def extract_facts(question: str) -> dict[str, object]:
    """Pull the facts rules need out of the question — copying a stated number, the
    one kind of step small models do reliably. Constrained to a schema; anything it
    cannot find is null, and a null becomes a question to the person, not a guess."""
    def prop(kind: str) -> dict[str, object]:
        if kind == "array":
            return {"type": ["array", "null"], "items": {"type": "number"}}
        return {"type": [kind, "null"]}

    schema: dict[str, object] = {
        "type": "object",
        "properties": {f.key: prop(f.kind) for f in rules.FACTS},
        "required": [f.key for f in rules.FACTS],
    }
    wanted = "\n".join(f"- {f.key}: {f.describe}" for f in rules.FACTS)
    prompt = (
        "Extract these facts from the person's message. Use only what the message "
        f"states; null if it does not state it.\n{wanted}\n\nMESSAGE: {question}"
    )
    try:
        raw = "".join(
            stream_chat(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                fmt=schema,
                max_tokens=settings.plan_max_tokens,
            )
        )
        got = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(got, dict):
        return {}
    # Kept only if the person actually said it. See rules._months_stated.
    return {
        f.key: got[f.key]
        for f in rules.FACTS
        if got.get(f.key) is not None and f.stated(got[f.key], question)
    }


def decide(question: str, prof: dict[str, object]) -> list[rules.Outcome | rules.Needs]:
    """Rules in play for this question, decided from the profile and the question.
    Extraction runs only when a rule could apply, so an unrelated question costs no
    model call. What the question states overrides the profile."""
    facts: dict[str, object] = dict(prof)
    hit = {k for k, rx in RULE_TRIGGERS.items() if rx.search(question)}
    if hit:
        facts.update(dict.fromkeys(hit, True))
        if STEM_EXTENSION.search(question):
            facts["mentions_stem"] = True
        facts.update(extract_facts(question))
    return rules.evaluate(facts)


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
                temperature=settings.plan_temperature,
                max_tokens=settings.plan_max_tokens,
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
