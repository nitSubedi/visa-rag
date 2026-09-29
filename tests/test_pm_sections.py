"""The Policy Manual is chunked on its own headings, not by token count.

The export carries Volume / Part / Chapter as <h1>, and inside each chapter 2,782 <h2>
and 1,716 <h3> sub-headings. The chunker split only on <h1> and packed each chapter by
token budget, so the Dhanasar test — which has its own heading, "D. National Interest
Waiver > 3. Overview of the Three Prongs" — landed at word 410 of a 528-word chunk that
opened in the preamble of the previous section. Models below 3B summarised that opening
and stopped; 3b did the same whenever the chunk came last in the slate. Handed the
section on its own, 1.5b went from 1/4 to 3/4 and 3b's position failure vanished.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from visa import chunk
from visa.search import section_key
from visa.sources import Source

SRC = Source(
    slug="uscis-policy-manual", title="PM", tier=3, fetcher="html", chunker="uscis-pm"
)
WORDS = " ".join(f"filler{i}" for i in range(60))
HTML = f"""
<h1>Volume 6 - Immigrants</h1><h1>Part F - Employment-Based Classifications</h1>
<h1>Chapter 5 - Advanced Degree or Exceptional Ability</h1>
<p>Chapter introduction. {WORDS}</p>
<h2>A. Advanced Degree Professionals</h2>
<h3>1. Eligibility</h3>
<p>Advanced degree eligibility text. {WORDS}</p>
<h2>D. National Interest Waiver of Job Offer</h2>
<h3>2. Eligibility for the National Interest Waiver</h3>
<p>Preamble about waivers. {WORDS}</p>
<h3>3. Overview of the Three Prongs</h3>
<p>USCIS may grant a national interest waiver if the proposed endeavor has both
substantial merit and national importance; the person is well positioned to advance
it; and on balance it would be beneficial to waive the job offer. {WORDS}</p>
<h1>Chapter 6 - Physician</h1><p>Physician text. {WORDS}</p>
"""


@pytest.fixture
def chunks(tmp_path: Path):
    f = tmp_path / "pm.html"
    f.write_text(HTML)
    return chunk.chunk_uscis_pm([f], SRC)


def test_a_section_gets_its_own_citation(chunks) -> None:
    prongs = [c for c in chunks if "substantial" in c.text]
    assert prongs, "the three-prong section must be chunked"
    assert all(c.citation == "USCIS PM Vol 6, Pt F, Ch 5, D.3" for c in prongs)


def test_the_answer_leads_its_chunk(chunks) -> None:
    """The measured failure was depth: the test sat at word 410. It must lead now."""
    c = next(c for c in chunks if "substantial" in c.text)
    body = c.text.split("\n", 1)[1]
    assert body.split().index("substantial") < 40


def test_sibling_sections_are_not_merged(chunks) -> None:
    prongs = next(c for c in chunks if "substantial" in c.text)
    assert "Preamble about waivers" not in prongs.text


def test_every_chunk_carries_its_heading_path(chunks) -> None:
    """Splitting out the paragraph alone cost 3b the source_is_policy check — the answer
    stopped saying where the test comes from. The path travels with the text."""
    c = next(c for c in chunks if "substantial" in c.text)
    head = c.text.split("\n", 1)[0]
    assert "National Interest Waiver" in head and "Three Prongs" in head


def test_text_before_the_first_section_keeps_the_chapter_citation(chunks) -> None:
    intro = next(c for c in chunks if "Chapter introduction" in c.text)
    assert intro.citation == "USCIS PM Vol 6, Pt F, Ch 5"


def test_chapters_stay_separate(chunks) -> None:
    doc = next(c for c in chunks if "Physician text" in c.text)
    assert doc.citation == "USCIS PM Vol 6, Pt F, Ch 6"


def test_the_section_cap_still_counts_a_whole_chapter() -> None:
    """Otherwise one chapter could fill the slate in pieces — the 204.12 crowding bug."""
    assert section_key("USCIS PM Vol 6, Pt F, Ch 5, D.3") == "USCIS PM Vol 6, Pt F, Ch 5"
    assert section_key("USCIS PM Vol 6, Pt F, Ch 5") == "USCIS PM Vol 6, Pt F, Ch 5"


def test_the_real_dhanasar_section_opens_with_the_test() -> None:
    raw = list(
        (Path.home() / ".visa" / "corpus" / "uscis-policy-manual" / "raw").glob("*")
    )
    if not raw:
        pytest.skip("corpus not fetched")
    real = chunk.chunk_uscis_pm(raw, SRC)
    # The concise statement of the test is the closing paragraph of section D.2, not the
    # opening of D.3 — a heading split alone left it at word 295. What matters is that a
    # short chunk carries all three prongs near its top.
    whole = [
        c
        for c in real
        if c.citation.startswith("USCIS PM Vol 6, Pt F, Ch 5, D.")
        and all(
            p in c.text for p in ("substantial merit", "well positioned", "on balance")
        )
    ]
    assert whole, "some NIW chunk must carry the complete three-prong test"
    best = min(whole, key=lambda c: c.text.split("\n", 1)[1].find("substantial merit"))
    body = best.text.split("\n", 1)[1].split()
    assert len(body) < 300
    assert (
        next(i for i, w in enumerate(body) if w == "merit" or w.startswith("merit")) < 150
    )
