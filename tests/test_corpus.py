"""Corpus-wide structural invariants.

`test_citations.py` locks specific paragraphs that were known to be wrong. These
tests instead assert properties that must hold for *every* section, because the
214.2 fix was verified on 214.2 and the same defect survived elsewhere: the ten
EB-1A criteria are currently filed under 8 CFR 204.5(i) — the outstanding
professor category — rather than 204.5(h)(3).

Note what does NOT work as an invariant: letter-sequence contiguity. The chunker
assigns letters in strict sequence, so promoting a roman sub-list to a top-level
paragraph *fills* the sequence and leaves no gap to detect.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import pytest

from visa import chunk
from visa.sources import Source

CFR_XML = Path.home() / ".visa" / "corpus" / "8-cfr" / "raw"
SRC = Source(
    slug="8-cfr", title="8 CFR", tier=2, fetcher="ecfr-xml", chunker="ecfr", url="x"
)
SEC_RE = re.compile(r"^(\d+) CFR § ([\d.]+)\(([a-z]+)\)$")
ROMAN_FIRST = {"i", "v", "x"}


def _xml() -> list[Path]:
    if not CFR_XML.exists():
        pytest.skip("corpus not fetched — run `visa init`")
    return list(CFR_XML.glob("*.xml"))


@pytest.fixture(scope="module")
def by_section() -> dict[str, dict[str, str]]:
    """{section: {paragraph_letter: full text}} for top-level letter paragraphs."""
    out: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for r in chunk.chunk_ecfr(_xml(), SRC):
        m = SEC_RE.match(r.citation)
        if m and len(m.group(3)) == 1:
            sec, letter = m.group(2), m.group(3)
            out[sec][letter] = out[sec].get(letter, "") + "\n" + r.text
    return out


def _strip_header(text: str) -> str:
    return re.sub(r"^8 CFR § [^\n]*\n?", "", text, flags=re.M).lstrip()


def test_no_roman_sublist_promoted_to_letter_paragraph(by_section) -> None:
    """A paragraph ending "...the following:" hands its list to the next letter.

    When that list is enumerated with roman numerals, "(i)" is indistinguishable
    from the letter that legitimately follows "(h)". Signature of the failure: the
    preceding paragraph ends mid-sentence AND this one opens with "(i)".
    """
    promoted = []
    for section, letters in sorted(by_section.items()):
        for letter, text in sorted(letters.items()):
            if letter not in ROMAN_FIRST:
                continue
            prev = letters.get(chr(ord(letter) - 1), "")
            body = _strip_header(text)
            if prev.rstrip().endswith(":") and re.match(r"\(i\)\s", body):
                promoted.append(f"{section}({letter})")
    assert not promoted, (
        f"{len(promoted)} section(s) mislabel a roman sub-list as a top-level "
        f"paragraph: {promoted}"
    )


def test_no_paragraph_promises_a_list_it_does_not_contain(by_section) -> None:
    """"...shall include the following:" must be followed by the following."""
    truncated = []
    for section, letters in sorted(by_section.items()):
        for letter, text in sorted(letters.items()):
            if text.rstrip().endswith(":"):
                truncated.append(f"{section}({letter})")
    assert not truncated, (
        f"{len(truncated)} paragraph(s) end mid-list, so the enumerated items are "
        f"absent from the corpus: {truncated}"
    )


def test_eb1a_criteria_are_filed_under_204_5_h(by_section) -> None:
    """The ten criteria are 204.5(h)(3)(i)-(x), not 204.5(i).

    204.5(i) is outstanding professors and researchers — a different classification
    with a different standard. Citing the criteria to it is the exact failure mode
    test_citations.py was written to prevent, recurring in another section.
    """
    s = by_section.get("204.5", {})
    h, i = s.get("h", "").lower(), s.get("i", "").lower()
    # The outstanding-researcher criteria at (i)(3) are worded almost identically
    # ("judge of the work of others", "authorship of scholarly books or articles"),
    # so discriminate on the criteria unique to extraordinary ability.
    for marker in (
        "lesser nationally or internationally recognized prizes",
        "artistic exhibitions or showcases",
        "commercial successes in the performing arts",
    ):
        assert marker in h, f"204.5(h) must contain the EB-1A criterion '{marker}'"
        assert marker not in i, f"204.5(i) must not carry the EB-1A criterion '{marker}'"
    assert "outstanding professors and researchers" in i, (
        "204.5(i) should be the outstanding professor or researcher paragraph"
    )


def test_every_paragraph_opens_with_its_own_letter(by_section) -> None:
    """The first chunk of paragraph (x) should begin "(x)"."""
    mismatched = []
    for section, letters in sorted(by_section.items()):
        for letter, text in sorted(letters.items()):
            body = _strip_header(text)
            if body and not body.startswith(f"({letter})"):
                mismatched.append(f"{section}({letter})")
    assert len(mismatched) <= 1, (
        f"{len(mismatched)} paragraph(s) do not open with their own letter: "
        f"{mismatched[:10]}"
    )


# ------------------------------------------------- enumerated provisions are splittable

EB1A_CRITERIA = (
    "lesser nationally or internationally recognized prizes",
    "membership in associations",
    "Published material about the alien",
    "as a judge of the work of others",
    "contributions of major significance",
    "authorship of scholarly articles",
    "artistic exhibitions or showcases",
    "leading or critical role",
    "commanded a high salary",
    "commercial successes in the performing arts",
)


@pytest.fixture(scope="module")
def cfr_chunks() -> list:
    return chunk.chunk_ecfr(_xml(), SRC)


def _carrying(chunks, phrase, citation="204.5(h)"):
    return [c for c in chunks if citation in c.citation and phrase in c.text]


def test_each_eb1a_criterion_lands_in_its_own_chunk(cfr_chunks) -> None:
    """Finding 10c: the ten criteria sat in one 703-token chunk covering definitions,
    the 3-of-10 gate, all ten tests and the employment clause. That embedding matches
    nothing sharply — querying criterion (v) by its own literal wording returned zero
    regulation, while the near-identically worded O-1 provision at 214.2(o), which IS
    finely chunked, won every EB-1A query. Size was never the defect; packing ten
    independent legal tests into one vector was."""
    for phrase in EB1A_CRITERIA:
        holders = _carrying(cfr_chunks, phrase)
        assert holders, f"criterion text missing from 204.5(h): {phrase!r}"
        others = [p for p in EB1A_CRITERIA if p != phrase]
        best = min(holders, key=lambda c: sum(o in c.text for o in others))
        crowd = sum(o in best.text for o in others)
        assert crowd <= 2, f"{phrase!r} shares a chunk with {crowd} other criteria"


def test_a_split_criterion_still_carries_the_three_of_ten_gate(cfr_chunks) -> None:
    """A criterion severed from "at least three of the following" is a trap: it reads
    as a requirement rather than one of ten alternatives. That misreading is exactly
    the error finding 10 recorded — a model calling absent membership fatal."""
    for phrase in ("contributions of major significance", "commanded a high salary"):
        holders = _carrying(cfr_chunks, phrase)
        assert any("at least three of the following" in c.text for c in holders), (
            f"no chunk carrying {phrase!r} retains the 3-of-10 stem"
        )


def test_splitting_never_invents_a_citation(cfr_chunks) -> None:
    """Pieces of 204.5(h) stay 204.5(h). A split that renumbers is finding 2b again."""
    for phrase in EB1A_CRITERIA:
        for c in _carrying(cfr_chunks, phrase):
            assert c.citation.strip().endswith("204.5(h)"), c.citation
