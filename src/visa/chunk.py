"""Section-aware chunking.

This is the quality lever. A naive 500-character splitter destroys the one thing that
makes legal retrieval useful: knowing which provision a passage came from. Every chunk
here carries its own citation, so an answer can point at "8 CFR 214.2(f)(10)" rather
than "somewhere in the regulations".
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config

WS = re.compile(r"\s+")
TAG = re.compile(r"<[^>]+>")
SCRIPT = re.compile(r"(?is)<(script|style)\b.*?</\1>")


def clean(s: str) -> str:
    return WS.sub(" ", s).strip()


def strip_html(s: str) -> str:
    return clean(TAG.sub(" ", SCRIPT.sub(" ", s)))


def ntokens(s: str) -> int:
    return int(len(s.split()) * 1.33)


def _pack(paras: list[str], budget: int = None, overlap: int = None) -> list[str]:
    """Group paragraphs into token-budgeted pieces, never splitting mid-paragraph
    unless a single paragraph exceeds the budget."""
    budget = budget or config.CHUNK_TOKENS
    overlap = overlap or config.CHUNK_OVERLAP
    out, cur, n = [], [], 0
    for p in paras:
        pt = ntokens(p)
        if pt > budget * 1.6:  # a genuinely huge paragraph — hard split on sentences
            for piece in _split_sentences(p, budget):
                out.append(piece)
            continue
        if n + pt > budget and cur:
            out.append(" ".join(cur))
            tail, tn = [], 0
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
    return [c for c in out if c.strip()]


def _split_sentences(text: str, budget: int) -> list[str]:
    sents = re.split(r"(?<=[.;:])\s+(?=[A-Z(])", text)
    out, cur, n = [], [], 0
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


def _mk(text, citation, title, src, path="", **kw) -> dict:
    return {
        "text": text, "citation": citation, "title": title,
        "shard": src.slug, "tier": src.tier, "kind": src.kind,
        "source_title": src.title, "path": path,
        "url": kw.get("url", src.all_urls[0] if src.all_urls else ""),
    }


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
        return len(cand) == len(cur) + 1 and cur == "z" * len(cur) \
            and cand == "a" * len(cand)
    if len(set(cur)) == 1 and len(set(cand)) == 1:      # (aa) -> (bb)
        return ord(cand[0]) - ord(cur[0]) == 1
    return False


def _toc_letters(el) -> list[tuple[str, str]]:
    """Long CFR sections open with their own table of contents listing the real
    top-level paragraphs. That is authoritative — far better than guessing, since
    sub-paragraphs use roman numerals that are indistinguishable from letters.

    Returns [("f", "Students in colleges, universities, ..."), ...].
    """
    for tab in el.iter("TABLE"):
        pairs = []
        for r in tab.iter("TR"):
            txt = clean(" ".join("".join(td.itertext()) for td in r.iter("TD")))
            m = re.match(r"^\(([a-z]{1,2})\)\s+(.+)$", txt)
            if m:
                pairs.append((m.group(1), clean(m.group(2))))
        if len(pairs) >= 3 and pairs[0][0] == "a":
            return pairs
    return []


def chunk_ecfr(paths: list[Path], src) -> list[dict]:
    """One logical unit per CFR section; oversized sections split on top-level
    paragraph letters so (f) Students and (o) Extraordinary ability stay distinct."""
    out = []
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
            expect = 0                      # pointer into the TOC sequence
            paras, cur_letter, group = [], "", []
            for child in el.iter():
                if child.tag == "HEAD" and not head:
                    head = clean("".join(child.itertext()))
                elif child.tag in ("P", "FP"):
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
                            ok = (expect < len(toc)
                                  and cand == toc[expect][0]
                                  and _opens_with(t[m.end():], toc[expect][1]))
                            if ok:
                                expect += 1
                        else:
                            ok = _next_in_sequence(cur_letter, cand)
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
                for piece in _pack(ps):
                    out.append(_mk(
                        f"{cite} — {label}\n{piece}",
                        cite, label, src,
                        path=f"Title {title_no} > Part {part} > § {sec}"
                             + (f"({letter})" if letter else ""),
                    ))
    return out


# ---------------------------------------------------- USCIS Policy Manual (HTML)

H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def chunk_uscis_pm(paths: list[Path], src) -> list[dict]:
    """The export is one flat HTML document; hierarchy is carried by sequential
    <h1> headings (Volume N / Part X / Chapter N)."""
    out = []
    for p in paths:
        html = p.read_text(encoding="utf-8", errors="ignore")
        marks = [(m.start(), m.end(), strip_html(m.group(1))) for m in H1.finditer(html)]
        vol = part = chap = ""
        for i, (s, e, head) in enumerate(marks):
            nxt = marks[i + 1][0] if i + 1 < len(marks) else len(html)
            body = strip_html(html[e:nxt])
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
            paras = [x for x in re.split(r"(?<=[.])\s+(?=[A-Z0-9])", body) if x.strip()]
            for piece in _pack(paras):
                out.append(_mk(f"{cite} — {chap}\n{piece}", cite, chap, src,
                               path=" > ".join(x for x in (vol, part, chap) if x)))
    return out


# ------------------------------------------------------------ US Code (8 U.S.C.)

def chunk_uscode(paths: list[Path], src) -> list[dict]:
    out = []
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
            for piece in _pack(paras or [body]):
                out.append(_mk(f"{cite} — {head_s}\n{piece}", cite, head_s, src,
                               path=f"8 U.S.C. {num_s}"))
    return out


# ------------------------------------------------------------------------- PDF

def chunk_pdf(paths: list[Path], src) -> list[dict]:
    from pypdf import PdfReader
    out = []
    for p in paths:
        if p.suffix.lower() != ".pdf":
            continue
        try:
            rd = PdfReader(str(p))
        except Exception:
            continue
        pages = []
        for i, pg in enumerate(rd.pages, 1):
            try:
                pages.append((i, clean(pg.extract_text() or "")))
            except Exception:
                continue
        for pno, text in pages:
            if len(text.split()) < 25:
                continue
            cite = f"{src.title} (p. {pno})"
            for piece in _pack([x for x in re.split(r"(?<=[.])\s+", text) if x.strip()]):
                out.append(_mk(f"{cite}\n{piece}", cite, p.stem, src, path=p.name))
    return out


# --------------------------------------------------------------- plain / local

def chunk_text(paths: list[Path], src) -> list[dict]:
    out = []
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
        for piece in _pack([x for x in re.split(r"(?<=[.])\s+", body) if x.strip()]):
            out.append(_mk(f"{p.name}\n{piece}", p.name, p.stem, src, path=p.name))
    return out


CHUNKERS = {
    "ecfr": chunk_ecfr,
    "uscis-pm": chunk_uscis_pm,
    "uscode": chunk_uscode,
    "pdf": chunk_pdf,
    "text": chunk_text,
}


def chunk_source(src) -> list[dict]:
    raw = src.dir / "raw"
    paths = sorted(raw.glob("*")) if raw.exists() else []
    fn = CHUNKERS.get(src.chunker, chunk_text)
    rows = fn(paths, src)
    for i, r in enumerate(rows):
        r["id"] = f"{src.slug}:{i}"
    return rows
