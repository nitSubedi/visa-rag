"""Section-aware chunking.

This is the quality lever. A naive 500-character splitter destroys the one thing that
makes legal retrieval useful: knowing which provision a passage came from. Every chunk
here carries its own citation, so an answer can point at "8 CFR 214.2(f)(10)" rather
than "somewhere in the regulations".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from .config import settings
from .models import Chunk
from .sources import Source

WS = re.compile(r"\s+")
TAG = re.compile(r"<[^>]+>")
SCRIPT = re.compile(r"(?is)<(script|style)\b.*?</\1>")


def clean(s: str) -> str:
    return WS.sub(" ", s).strip()


def strip_html(s: str) -> str:
    return clean(TAG.sub(" ", SCRIPT.sub(" ", s)))


def ntokens(s: str) -> int:
    return int(len(s.split()) * 1.33)


def _pack(
    paras: list[str], budget: int | None = None, overlap: int | None = None
) -> list[tuple[str, str]]:
    """Group paragraphs into token-budgeted pieces, never splitting mid-paragraph
    unless a single paragraph exceeds the budget."""
    budget = budget or settings.chunk_tokens
    overlap = overlap or settings.chunk_overlap
    out: list[str] = []
    cur: list[str] = []
    n = 0
    for p in paras:
        pt = ntokens(p)
        if pt > budget * 1.6:  # a genuinely huge paragraph — hard split on sentences
            for piece in _split_sentences(p, budget):
                out.append(piece)
            continue
        if n + pt > budget and cur:
            out.append(" ".join(cur))
            tail: list[str] = []
            tn = 0
            for q in reversed(cur):  # carry a little context forward
                tn += ntokens(q)
                tail.insert(0, q)
                if tn >= overlap:
                    break
            cur, n = tail[:], tn
        cur.append(p)
        n += pt
    if cur:
        out.append(" ".join(cur))
    return [p for c in out if c.strip() for p in _split_enumerated(c)]


# A provision that enumerates alternative tests is not one passage but N of them.
# 8 CFR 204.5(h) packed the ten EB-1A criteria, the definition, the 3-of-10 gate and
# the employment clause into a single chunk. It never broke the token budget — 703
# tokens against 700 — so nothing here was misbehaving. But one vector standing for
# ten independent legal tests matches none of them sharply: criterion (v) could not be
# retrieved by its own literal wording, while 214.2(o) — the O-1 nonimmigrant
# provision, worded near-identically and finely chunked — won every EB-1A query. The
# correct provision lost to the wrong one because the wrong one was better chunked.
#
# Split at the enumeration, and carry the stem into every piece: a criterion severed
# from "at least three of the following" reads as a requirement rather than one of ten
# alternatives, which is precisely the misreading finding 10 recorded.
ENUM_ITEM = re.compile(
    r"(?<![A-Za-z])\((?:i{1,3}|iv|vi{1,3}|ix|xi{0,3}|v|x)\)\s+(?=[A-Z])"
)
ENUM_MIN_ITEMS = 4  # three enumerated items is ordinary prose; four is a test
ENUM_MIN_TOKENS = 400  # below this the packed embedding is still sharp enough
ENUM_STEM_TOKENS = 120  # enough to carry the gate sentence that governs the items


def _split_enumerated(text: str) -> list[tuple[str, str]]:
    """Split an enumerated provision into its stem plus one piece per item.

    Returns (served, embedded) pairs. Each item is *served* with the stem's tail so it
    still reads as one of N alternatives, but *embedded* alone: the shared preamble is
    the same in every piece and dominates the vector, which is what kept criterion (v)
    at 0.631 against a Policy Manual chunk at 0.699 despite being the governing law.
    """
    marks = list(ENUM_ITEM.finditer(text))
    if len(marks) < ENUM_MIN_ITEMS or ntokens(text) < ENUM_MIN_TOKENS:
        return [(text, text)]

    stem = text[: marks[0].start()].rstrip()
    if not stem.strip():
        return [(text, text)]

    # The citation header is the chunk's first line; every piece needs it back.
    head, _, body = stem.partition("\n")
    if "\u00a7" not in head and "SEC." not in head:
        head, body = "", stem
    lead = " ".join(x for x in (head, " ".join(body.split()[-ENUM_STEM_TOKENS:])) if x)

    bounds = [m.start() for m in marks] + [len(text)]
    out = [(stem, stem)]
    for i in range(len(marks)):
        item = text[bounds[i] : bounds[i + 1]].strip()
        if item:
            out.append((f"{lead.strip()} {item}".strip(), item))
    return out


def _split_sentences(text: str, budget: int) -> list[str]:
    sents = re.split(r"(?<=[.;:])\s+(?=[A-Z(])", text)
    out: list[str] = []
    cur: list[str] = []
    n = 0
    for s in sents:
        st = ntokens(s)
        if n + st > budget and cur:
            out.append(" ".join(cur))
            cur, n = [], 0
        cur.append(s)
        n += st
    if cur:
        out.append(" ".join(cur))
    return out


def _embed_override(served: str, embedded: str) -> str:
    """Empty when a piece is embedded exactly as served, so anything the enumerated
    split did not touch keeps its existing vector unchanged."""
    return "" if embedded == served else embedded


def _mk(
    text: str,
    citation: str,
    title: str,
    src: Source,
    path: str = "",
    url: str = "",
    embed_text: str = "",
) -> Chunk:
    return Chunk(
        text=text,
        embed_text=embed_text,
        citation=citation,
        title=title,
        shard=src.slug,
        tier=src.tier,
        kind=src.kind,
        source_title=src.title,
        path=path,
        url=url or (src.all_urls[0] if src.all_urls else ""),
    )


# ---------------------------------------------------------------- eCFR (8 CFR)


def _norm_words(s: str, n: int) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if w][:n]


def _opens_with(body: str, heading: str, n: int = 3) -> bool:
    """Does this paragraph's text actually begin with the heading the TOC lists?

    This is what separates a genuine top-level paragraph from a roman-numeral
    sub-paragraph that happens to share its letter.
    """
    h = _norm_words(heading, n)
    if not h:
        return False
    return _norm_words(body, len(h)) == h


def _next_in_sequence(cur: str, cand: str) -> bool:
    """Fallback when a section has no table of contents: accept only the strict next
    label in the (a), (b) ... (z), (aa), (bb) sequence."""
    if not cur:
        return cand == "a"
    if len(cur) != len(cand):
        return (
            len(cand) == len(cur) + 1
            and cur == "z" * len(cur)
            and cand == "a" * len(cand)
        )
    if len(set(cur)) == 1 and len(set(cand)) == 1:  # (aa) -> (bb)
        return ord(cand[0]) - ord(cur[0]) == 1
    return False


def _promises_a_list(group: list[str]) -> bool:
    """Does the paragraph so far end by handing its items to what follows?

    "...or at least three of the following:" means the next "(i)" opens that list
    rather than the section's paragraph (i). With a table of contents `_opens_with`
    settles this; without one, strict sequencing accepts the roman numeral because
    it genuinely is the next letter. 204.5 has no table of contents, which is how
    the ten EB-1A criteria came to be filed under 204.5(i) — outstanding professors
    and researchers — instead of 204.5(h)(3).
    """
    return "".join(group).rstrip().endswith(":")


def _toc_letters(el: ET.Element) -> list[tuple[str, str]]:
    """Long CFR sections open with their own table of contents listing the real
    top-level paragraphs. That is authoritative — far better than guessing, since
    sub-paragraphs use roman numerals that are indistinguishable from letters.

    Returns [("f", "Students in colleges, universities, ..."), ...].
    """
    for tab in el.iter("TABLE"):
        pairs: list[tuple[str, str]] = []
        for r in tab.iter("TR"):
            txt = clean(" ".join("".join(td.itertext()) for td in r.iter("TD")))
            m = re.match(r"^\(([a-z]{1,2})\)\s+(.+)$", txt)
            if m:
                pairs.append((m.group(1), clean(m.group(2))))
        if len(pairs) >= 3 and pairs[0][0] == "a":
            return pairs
    return []


def chunk_ecfr(paths: list[Path], src: Source) -> list[Chunk]:
    """One logical unit per CFR section; oversized sections split on top-level
    paragraph letters so (f) Students and (o) Extraordinary ability stay distinct."""
    out: list[Chunk] = []
    for p in paths:
        if p.suffix.lower() != ".xml":
            continue
        root = ET.parse(p).getroot()
        title_no = "8"
        for div1 in root.iter("DIV1"):
            title_no = div1.get("N", "8")
            break
        part = ""
        for el in root.iter():
            if el.tag == "DIV5":
                part = el.get("N", "")
            if el.tag != "DIV8":
                continue
            sec = el.get("N", "").strip()
            head = ""
            toc = _toc_letters(el)
            expect = 0  # pointer into the TOC sequence
            paras: list[tuple[str, list[str]]] = []
            cur_letter = ""
            group: list[str] = []
            for child in el.iter():
                if child.tag == "HEAD" and not head:
                    head = clean("".join(child.itertext()))
                # eCFR flush paragraphs carry a depth suffix — FP-1, FP-2 — and a
                # bare "FP" never appears. Matching only ("P", "FP") dropped 1,059
                # elements of Title 8, which is where enumerated list items live:
                # 264.1(a) promised a list of registration forms and delivered none.
                elif child.tag == "P" or child.tag.startswith("FP"):
                    t = clean("".join(child.itertext()))
                    if not t:
                        continue
                    m = re.match(r"^\(([a-z]{1,2})\)\s", t)
                    if m:
                        cand = m.group(1)
                        if toc:
                            # The letter alone is ambiguous: (h) H-1B is full of roman
                            # "(i)" sub-paragraphs, and the TOC's next expected letter
                            # is also "i". So require the body text to actually open
                            # with the heading the TOC gives for that paragraph.
                            ok = (
                                expect < len(toc)
                                and cand == toc[expect][0]
                                and _opens_with(t[m.end() :], toc[expect][1])
                            )
                            if ok:
                                expect += 1
                        else:
                            ok = _next_in_sequence(cur_letter, cand)
                            # (i), (v) and (x) are both letters and roman numerals.
                            # Where the preceding text promises a list, they open
                            # that list rather than a new top-level paragraph.
                            if ok and cand in ("i", "v", "x") and _promises_a_list(group):
                                ok = False
                        if not ok:
                            m = None
                    if m and group:
                        paras.append((cur_letter, group))
                        group = []
                    if m:
                        cur_letter = m.group(1)
                    group.append(t)
            if group:
                paras.append((cur_letter, group))
            if not paras:
                continue
            heading = re.sub(r"^§+\s*[\d.]+\s*", "", head).strip(" .")
            toc_head = dict(toc)
            for letter, ps in paras:
                cite = f"{title_no} CFR § {sec}" + (f"({letter})" if letter else "")
                sub = toc_head.get(letter, "")
                label = f"{heading} — {sub}" if sub else heading
                for piece, emb in _pack(ps):
                    out.append(
                        _mk(
                            f"{cite} — {label}\n{piece}",
                            cite,
                            label,
                            src,
                            path=f"Title {title_no} > Part {part} > § {sec}"
                            + (f"({letter})" if letter else ""),
                            embed_text=_embed_override(piece, emb),
                        )
                    )
    return out


# ---------------------------------------------------- USCIS Policy Manual (HTML)

H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


# The Policy Manual carries its own footnote markers — 12,778 of them across 75% of
# its chunks. The prompt asks the model to cite as [1], [2] matching OUR source
# numbering, then hands it text littered with [64], [128] and the rest, and it cannot
# tell them apart. Every fabricated citation observed came out of this. Worse, a
# footnote [3] copied into an answer is *in range*, so verify_citations passes it while
# it points somewhere else entirely.
FOOTNOTE = re.compile(r"\s*\[\d{1,3}\]")


# Inside a chapter the Manual is divided by its own authors: <h2> "D. National Interest
# Waiver of Job Offer", <h3> "3. Overview of the Three Prongs". This chunker used to split
# only on <h1> and pack each chapter by token budget, throwing that structure away — the
# Dhanasar test landed at word 410 of a 528-word chunk opening in the previous section's
# preamble, and models below 3B summarised the opening and stopped. Cut where the
# document already cuts.
H23 = re.compile(r"<(h[23])[^>]*>(.*?)</\1>", re.S | re.I)
SECTION_LABEL = re.compile(r"^([A-Z]|\d{1,2})\.\s")
MIN_SECTION_WORDS = 12  # a heading with a line under it is not a passage


def _label(heading: str) -> str:
    m = SECTION_LABEL.match(heading)
    return m.group(1) if m else ""


def _pm_sections(seg: str) -> list[tuple[str, str, str]]:
    """(h2, h3, html) for the intro and each sub-section of one chapter's HTML."""
    marks = list(H23.finditer(seg))
    out = [("", "", seg[: marks[0].start()] if marks else seg)]
    h2 = h3 = ""
    for i, m in enumerate(marks):
        title = strip_html(m.group(2)).strip()
        if title:  # the export opens some chapters with an empty <h2> wrapper
            if m.group(1).lower() == "h2":
                h2, h3 = title, ""
            else:
                h3 = title
        end = marks[i + 1].start() if i + 1 < len(marks) else len(seg)
        out.append((h2, h3, seg[m.end() : end]))
    return out


def chunk_uscis_pm(paths: list[Path], src: Source) -> list[Chunk]:
    """The export is one flat HTML document. Volume / Part / Chapter are sequential
    <h1> headings; sections and sub-sections inside a chapter are <h2> and <h3>, and each
    becomes its own citation — "USCIS PM Vol 6, Pt F, Ch 5, D.3"."""
    out: list[Chunk] = []
    for p in paths:
        html = p.read_text(encoding="utf-8", errors="ignore")
        marks = [(m.start(), m.end(), strip_html(m.group(1))) for m in H1.finditer(html)]
        vol = part = chap = ""
        for i, (_start, e, head) in enumerate(marks):
            nxt = marks[i + 1][0] if i + 1 < len(marks) else len(html)
            body = FOOTNOTE.sub("", strip_html(html[e:nxt]))
            if re.match(r"^Volume\s", head):
                vol, part, chap = head, "", ""
                continue
            if re.match(r"^Part\s", head):
                part, chap = head, ""
                continue
            if re.match(r"^Chapter\s", head):
                chap = head
            else:
                continue
            if len(body.split()) < 40:
                continue
            v = re.match(r"Volume\s+(\S+)", vol)
            pt = re.match(r"Part\s+(\S+)", part)
            ch = re.match(r"Chapter\s+(\S+)", chap)
            cite = "USCIS PM"
            if v:
                cite += f" Vol {v.group(1)}"
            if pt:
                cite += f", Pt {pt.group(1).rstrip('-')}"
            if ch:
                cite += f", Ch {ch.group(1).rstrip('-')}"
            for h2, h3, sec_html in _pm_sections(html[e:nxt]):
                sec = FOOTNOTE.sub("", strip_html(sec_html))
                if len(sec.split()) < MIN_SECTION_WORDS:
                    continue
                a, b = _label(h2), _label(h3)
                suffix = f"{a}.{b}" if a and b else a or b
                sec_cite = f"{cite}, {suffix}" if suffix else cite
                # The heading path rides with the text: split out of its chapter, a
                # section otherwise stops saying where its rule comes from.
                heads = " > ".join(x for x in (chap, h2, h3) if x)
                paras = [
                    x for x in re.split(r"(?<=[.])\s+(?=[A-Z0-9])", sec) if x.strip()
                ]
                for piece, emb in _pack(paras, budget=settings.pm_chunk_tokens):
                    out.append(
                        _mk(
                            f"{sec_cite} — {heads}\n{piece}",
                            sec_cite,
                            h3 or h2 or chap,
                            src,
                            path=" > ".join(x for x in (vol, part, chap, h2, h3) if x),
                            embed_text=_embed_override(piece, emb),
                        )
                    )
    return out


# ------------------------------------------------------------ US Code (8 U.S.C.)


def chunk_uscode(paths: list[Path], src: Source) -> list[Chunk]:
    out: list[Chunk] = []
    for p in paths:
        if p.suffix.lower() != ".xml":
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"<section\b[^>]*>(.*?)</section>", txt, re.S):
            blk = m.group(1)
            num = re.search(r"<num[^>]*>(.*?)</num>", blk, re.S)
            hd = re.search(r"<heading[^>]*>(.*?)</heading>", blk, re.S)
            num_s = strip_html(num.group(1)) if num else ""
            head_s = strip_html(hd.group(1)) if hd else ""
            body = strip_html(blk)
            if len(body.split()) < 30:
                continue
            cite = f"8 U.S.C. {num_s}".strip()
            paras = [x for x in re.split(r"(?<=[.;])\s+(?=\()", body) if x.strip()]
            for piece, emb in _pack(paras or [body]):
                out.append(
                    _mk(
                        f"{cite} — {head_s}\n{piece}",
                        cite,
                        head_s,
                        src,
                        path=f"8 U.S.C. {num_s}",
                        embed_text=_embed_override(piece, emb),
                    )
                )
    return out


# ------------------------------------------------------------------------- PDF


def chunk_pdf(paths: list[Path], src: Source) -> list[Chunk]:
    from pypdf import PdfReader

    out: list[Chunk] = []
    for p in paths:
        if p.suffix.lower() != ".pdf":
            continue
        try:
            rd = PdfReader(str(p))
        except Exception:
            continue
        pages: list[tuple[int, str]] = []
        for i, pg in enumerate(rd.pages, 1):
            try:
                pages.append((i, clean(pg.extract_text() or "")))
            except Exception:
                continue
        for pno, text in pages:
            if len(text.split()) < 25:
                continue
            cite = f"{src.title} (p. {pno})"
            for piece, emb in _pack(
                [x for x in re.split(r"(?<=[.])\s+", text) if x.strip()]
            ):
                out.append(
                    _mk(
                        f"{cite}\n{piece}",
                        cite,
                        p.stem,
                        src,
                        path=p.name,
                        embed_text=_embed_override(piece, emb),
                    )
                )
    return out


# --------------------------------------------------------------- plain / local


def chunk_text(paths: list[Path], src: Source) -> list[Chunk]:
    out: list[Chunk] = []
    for p in paths:
        if p.suffix.lower() in (".pdf",):
            out += chunk_pdf([p], src)
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if p.suffix.lower() in (".html", ".htm", ".xml"):
            body = strip_html(body)
        body = clean(body)
        if not body:
            continue
        for piece, emb in _pack(
            [x for x in re.split(r"(?<=[.])\s+", body) if x.strip()]
        ):
            out.append(
                _mk(
                    f"{p.name}\n{piece}",
                    p.name,
                    p.stem,
                    src,
                    path=p.name,
                    embed_text=_embed_override(piece, emb),
                )
            )
    return out


CHUNKERS: dict[str, Callable[[list[Path], Source], list[Chunk]]] = {
    "ecfr": chunk_ecfr,
    "uscis-pm": chunk_uscis_pm,
    "uscode": chunk_uscode,
    "pdf": chunk_pdf,
    "text": chunk_text,
}


def chunk_source(src: Source) -> list[Chunk]:
    raw = src.dir / "raw"
    paths = sorted(raw.glob("*")) if raw.exists() else []
    fn = CHUNKERS.get(src.chunker, chunk_text)
    rows = fn(paths, src)
    for i, r in enumerate(rows):
        r.id = f"{src.slug}:{i}"
    return rows
