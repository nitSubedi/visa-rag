"""Provisions that are in the corpus but not in force.

The eCFR publishes regulations as amended and encodes nothing about litigation status,
so a nationwide injunction leaves the corpus simultaneously current and wrong — every
freshness signal green while the text quoted is not the law. Staleness at least
announces itself; this does not, and refreshing is what introduces it.

Every other guardrail in this project compares the answer to the corpus. None of them
can see that the corpus itself is not in force. This module is the exception.

The network boundary is unchanged: the register is *fetched* with the corpus and then
consulted locally. Lookup takes a citation, never the user's question, so no part of
someone's situation participates in the check.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import settings

# "8 CFR § 214.2(f)(5)" -> ("8", "CFR", "214.2", ["f", "5"])
CITE_RE = re.compile(
    r"^\s*(\d{1,2})\s*(CFR|C\.F\.R\.|U\.?S\.?C\.?)\s*§?\s*([\d.]+?)\s*((?:\([a-z0-9]{1,4}\))*)\s*$",
    re.I,
)
PART_RE = re.compile(r"\(([a-z0-9]{1,4})\)", re.I)


def _parse(citation: str) -> tuple[str, str, str, list[str]] | None:
    """Split a citation into title, corpus, section and its parenthesised parts.

    Parsing rather than string-prefixing is the point: "8 CFR 214.2" prefixes the *text*
    of "8 CFR 214.20" and "214.21" while governing neither of them. A register that
    warns on an unrelated provision teaches people to ignore warnings.
    """
    m = CITE_RE.match(citation)
    if not m:
        return None
    corpus = "usc" if m.group(2).upper().replace(".", "").startswith("US") else "cfr"
    parts = [x.lower() for x in PART_RE.findall(m.group(4))]
    return m.group(1), corpus, m.group(3), parts


def _mentions(text: str, path: list[str], depth: int) -> bool:
    """Does this passage actually contain the sub-paragraph the register names?

    The corpus cites at paragraph level — "8 CFR § 214.2(f)" is the entire F-1
    paragraph — so the citation cannot say which sub-paragraph a chunk's slice of text
    came from. Matching on the citation alone therefore condemned filing rules that
    were never enjoined (finding 12). The enumerator in the text is what distinguishes
    them, so look for it the same way the chunker recognises one: a marker followed by
    the start of a sentence.
    """
    marker = path[depth]
    return re.search(rf"\({re.escape(marker)}\)\s*[A-Z(]", text) is not None


def covers(provision: str, citation: str, text: str | None = None) -> bool:
    """Does a registered provision reach the passage a chunk was cited under?

    Title, corpus and section must agree, and one paragraph path must be a prefix of the
    other. Where the register is *broader* than the citation, that settles it — every
    slice of the paragraph is covered.

    Where the register is *more specific* than the citation, the citation alone cannot
    decide, and `text` is consulted. With no text to inspect the answer is yes, because
    the two errors are not symmetric: withholding law that is in force costs the model a
    source, while quoting suspended law as current costs someone their status.
    """
    a, b = _parse(provision), _parse(citation)
    if a is None or b is None:
        return False
    if (a[0], a[1], a[2]) != (b[0], b[1], b[2]):
        return False
    reg_path, cite_path = a[3], b[3]
    short, long = sorted((reg_path, cite_path), key=len)
    if long[: len(short)] != short:
        return False
    if len(reg_path) <= len(cite_path):  # register is at or above the citation
        return True
    if text is None:
        return True
    return _mentions(text, reg_path, len(cite_path))


def suspended(
    citation: str, text: str | None, entries: list[Entry] | None = None
) -> Entry | None:
    """The order suspending this passage, if any."""
    entries = entries if entries is not None else load()
    for e in entries:
        if any(covers(p, citation, text) for p in e.provisions):
            return e
    return None


@dataclass(frozen=True)
class Entry:
    """One court order, and the provisions it takes out of force."""

    provisions: tuple[str, ...]
    status: str
    case: str
    docket: str
    court: str
    order_date: dt.date
    scope: str
    rule: str
    instead: str
    not_covered: str
    source_url: str
    checked: dt.date

    @property
    def age_days(self) -> int:
        return (dt.date.today() - self.checked).days

    @property
    def is_stale(self) -> bool:
        return self.age_days > settings.register_refresh_days

    def headline(self) -> str:
        return (
            f"{self.status.upper()}: {self.case}, {self.docket}, "
            f"{self.order_date} ({self.scope})"
        )


def _path() -> Path:
    """The fetched copy if there is one, otherwise the copy shipped with the code.

    A fresh install is never unprotected, and an unreachable update URL degrades to the
    shipped floor rather than to silence.
    """
    fetched = settings.register / "injunctions.toml"
    if fetched.exists():
        return fetched
    return settings.source_defs / "injunctions.toml"


_CACHE: list[Entry] | None = None


def clear_cache() -> None:
    """Tests and `visa refresh` change the register underneath a live process."""
    global _CACHE
    _CACHE = None


def load(path: Path | None = None) -> list[Entry]:
    """Cached: search() consults the register per hit and search_many calls search once
    per issue, so an uncached read would reopen the file dozens of times per answer."""
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE
    p = path or _path()
    if not p.exists():
        return []
    raw = tomllib.loads(p.read_text())
    out = []
    for e in raw.get("entry", []):
        out.append(
            Entry(
                provisions=tuple(e.get("provisions", ())),
                status=e.get("status", "unknown"),
                case=e.get("case", ""),
                docket=e.get("docket", ""),
                court=e.get("court", ""),
                order_date=e["order_date"],
                scope=e.get("scope", ""),
                rule=e.get("rule", ""),
                instead=e.get("instead", ""),
                not_covered=e.get("not_covered", ""),
                source_url=e.get("source_url", ""),
                checked=e["checked"],
            )
        )
    if path is None:
        _CACHE = out
    return out


def affecting(citations: list[str], entries: list[Entry] | None = None) -> list[Entry]:
    """Entries touching any of these citations, in register order."""
    entries = entries if entries is not None else load()
    out = []
    for e in entries:
        if any(covers(p, c) for p in e.provisions for c in citations):
            out.append(e)
    return out


def note_for_prompt(entries: list[Entry]) -> str:
    """A block for the prompt, so the model cannot assert law that is not in force.

    Placed with the facts rather than the sources: this is not another passage to weigh,
    it overrides passages. Warn rather than refuse — someone needs to see both what the
    text says and that it is enjoined.
    """
    if not entries:
        return ""
    lines = ["NOT IN FORCE — the sources below include provisions a court has suspended."]
    for e in entries:
        lines.append(f"· {e.headline()}")
        lines.append(f"  Provisions: {', '.join(e.provisions)}")
        if e.instead:
            lines.append(f"  What governs instead: {' '.join(e.instead.split())}")
        if e.not_covered:
            lines.append(f"  Still in force: {' '.join(e.not_covered.split())}")
    lines.append(
        "Do not state these provisions as current law. Say plainly that they are "
        "suspended, name what governs instead, and cite the order."
    )
    return "\n".join(lines)
