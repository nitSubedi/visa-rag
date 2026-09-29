"""Section numbers with letters and hyphens, which `[\\d.]+` could not read."""

from __future__ import annotations

import pytest

from visa import answer, register
from visa.search import section_key


@pytest.mark.parametrize(
    ("citation", "section"),
    [
        ("8 CFR § 214.2(f)(5)", "214.2"),
        ("8 CFR § 274a.12(c)", "274a.12"),
        ("8 CFR § 245a.4(b)", "245a.4"),
        ("8 U.S.C. § 1101.", "1101"),
        ("8 U.S.C. § 1324a.", "1324a"),
        ("8 U.S.C. § 1440\u20131.", "1440\u20131"),  # en dash, as in the corpus
    ],
)
def test_every_section_shape_in_the_corpus_parses(citation: str, section: str) -> None:
    parsed = register._parse(citation)
    assert parsed is not None and parsed[2] == section


def test_lettered_parts_are_capped_per_section_not_per_part() -> None:
    """Before, every 274a section shared the key "8 CFR § 274"."""
    assert section_key("8 CFR § 274a.12(c)") != section_key("8 CFR § 274a.1(a)")
    assert section_key("8 CFR § 274a.12(c)") == section_key("8 CFR § 274a.12(b)")


def test_a_cited_lettered_section_is_read_whole() -> None:
    """Read as "274a", an invented 274a.99 would pass the fabricated-citation check."""
    assert answer.CITE_RE.findall("8 CFR 274a.12(c)(3)") == [("8", "274a.12(c)(3)")]
