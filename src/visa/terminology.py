"""The names people use, mapped to the words the law is written in.

People ask about "EB-1A", "CPT", "NIW". The regulations never say those: 8 CFR 204.5(h)
is written as "alien of extraordinary ability in the sciences, arts, education, business,
or athletics", and the one-year CPT rule as "curricular practical training". "EB-1A",
"EB-2", "NIW" and "O-1A" appear in none of the 6,391 regulation chunks. So keyword search
could not connect the question to the provision that governs it: asked about "full-time
CPT", retrieval did not return the one-year rule in its top 300; asked the same thing in
the regulation's own words, it ranked first. The same gap buried 204.5(h) at rank 7-16
behind Policy Manual chapters that happen to say "EB-1".

This expands the *retrieval query* only — never the prompt — so it changes what is
searched for, not what the model is told.

Every entry is sourced, because a wrong expansion would steer retrieval confidently to
the wrong law:

  corpus  the defining text is in the indexed corpus, in the form "Full Term (ABBR)" or
          equivalent, and tests/test_terminology.py checks the quote verbatim offline
  uscis   the definition is on a uscis.gov page, quoted; not checkable offline

and every expansion must occur verbatim in the corpus, or it could not help retrieval.

Matching is case-sensitive for abbreviations that are also English words ("OPT", "STEM",
"D/S"), so "opt out" and "stem from" are never touched, and case-insensitive for those
that are not ("cpt", "eb1a").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STEM_PATHWAYS = (
    "https://www.uscis.gov/working-in-the-united-states/stem-employment-pathways/"
    "immigrant-pathways-for-stem-employment-in-the-united-states"
)


@dataclass(frozen=True)
class Term:
    abbr: str
    pattern: str  # how the abbreviation is recognised in a question
    expansion: str  # the words the law uses; must occur verbatim in the corpus
    source_kind: str  # corpus | uscis
    source: str  # a citation in the corpus, or a URL
    quote: str  # the defining text, verbatim
    case_sensitive: bool = False  # True where the lowercase form is an English word


TERMS: tuple[Term, ...] = (
    Term(
        "CPT",
        r"\bCPT\b",
        "curricular practical training",
        "corpus",
        "USCIS PM Vol 2, Pt F, Ch 5, A",
        "Curricular Practical Training (CPT)",
    ),
    Term(
        "OPT",
        r"\bOPT\b",
        "optional practical training",
        "corpus",
        "8 CFR § 274a.12(c)",
        "Optional Practical Training (OPT)",
        case_sensitive=True,
    ),
    Term(
        "EAD",
        r"\bEADs?\b",
        "employment authorization document",
        "corpus",
        "8 CFR § 106.2(a)",
        "Employment Authorization Document (EAD)",
    ),
    Term(
        "PDSO",
        r"\bPDSOs?\b",
        "principal designated school official",
        "corpus",
        "8 CFR § 214.3(l)",
        "Principal Designated School Official (PDSO)",
    ),
    Term(
        "DSO",
        r"\bDSOs?\b",
        "designated school official",
        "corpus",
        "8 CFR § 214.3(l)",
        "Designated School Official (DSO)",
    ),
    Term(
        "SEVIS",
        r"\bSEVIS\b",
        "Student and Exchange Visitor Information System",
        "corpus",
        "8 CFR § 214.2(f)",
        "Student and Exchange Visitor Information System (SEVIS)",
    ),
    Term(
        "SEVP",
        r"\bSEVP\b",
        "Student and Exchange Visitor Program",
        "corpus",
        "8 CFR § 103.7(d)",
        "Student and Exchange Visitor Program (SEVP)",
    ),
    Term(
        "D/S",
        r"(?<![A-Za-z])D/S(?![A-Za-z])",
        "duration of status",
        "corpus",
        "8 CFR § 245a.4(b)",
        "Duration of Status (D/S)",
        case_sensitive=True,
    ),
    Term(
        "RFE",
        r"\bRFEs?\b",
        "request for evidence",
        "corpus",
        "USCIS PM Vol 1, Pt B, Ch 2, A",
        "Request for Evidence (RFE)",
    ),
    Term(
        "NOID",
        r"\bNOIDs?\b",
        "notice of intent to deny",
        "corpus",
        "USCIS PM Vol 1, Pt B, Ch 2, A",
        "Notice of Intent to Deny (NOID)",
    ),
    Term(
        "STEM",
        r"\bSTEM\b",
        "science, technology, engineering and mathematics",
        "corpus",
        "8 CFR § 214.1(a)",
        "Science, Technology, Engineering and Mathematics (STEM)",
        case_sensitive=True,
    ),
    Term(
        "I-94",
        r"\bI-?94\b",
        "arrival-departure record",
        "corpus",
        "8 CFR § 212.1(q)",
        "Arrival-Departure Record (CBP Form I-94)",
    ),
    Term(
        "EB-1A",
        r"\bEB[- ]?1A\b",
        "extraordinary ability in the sciences, arts, education, business, or athletics",
        "uscis",
        STEM_PATHWAYS,
        "You may be eligible for the EB-1A extraordinary ability immigrant visa "
        "classification if you have extraordinary ability in the sciences, arts, "
        "education, business, or athletics",
    ),
    Term(
        "EB-1B",
        r"\bEB[- ]?1B\b",
        "outstanding professors and researchers",
        "uscis",
        STEM_PATHWAYS,
        "EB-1B Outstanding Professors and Researchers",
    ),
    Term(
        "EB-1C",
        r"\bEB[- ]?1C\b",
        "multinational executives and managers",
        "uscis",
        STEM_PATHWAYS,
        "EB-1C Multinational Managers and Executives",
    ),
    Term(
        "EB-1",
        r"\bEB[- ]?1(?![A-Za-z0-9])",
        "extraordinary ability, outstanding professors or researchers, and "
        "multinational executives or managers",
        "corpus",
        "USCIS PM Vol 6, Pt E, Ch 2",
        "extraordinary ability, outstanding professors or researchers, and "
        "multinational executives or managers (EB-1)",
    ),
    Term(
        "EB-2",
        r"\bEB[- ]?2(?![A-Za-z0-9])",
        "members of the professions holding advanced degrees",
        "corpus",
        "USCIS PM Vol 6, Pt E, Ch 2",
        "members of the professions holding advanced degrees and persons of exceptional "
        "ability (EB-2)",
    ),
    # The corpus defines the abbreviation inside "Physician National Interest Waiver
    # (NIW)" — a physician NIW is one kind of national interest waiver, so the letters
    # mean the same thing; USCIS defines the general term on STEM_PATHWAYS ("This
    # self-petition ... is known as a national interest waiver").
    Term(
        "NIW",
        r"\bNIW\b",
        "national interest waiver",
        "corpus",
        "USCIS PM Vol 2, Pt D, Ch 3",
        "National Interest Waiver (NIW)",
    ),
    Term(
        "O-1A",
        r"\bO[- ]?1A\b",
        "extraordinary ability in the sciences, education, business, or athletics",
        "corpus",
        "USCIS PM Vol 2, Pt M, Ch 2, B",
        "O-1 Extraordinary Ability in Sciences, Education, Business, or Athletics "
        "(commonly referred to as O-1A)",
    ),
)

_COMPILED = tuple(
    (t, re.compile(t.pattern, 0 if t.case_sensitive else re.IGNORECASE)) for t in TERMS
)


def expand(query: str) -> str:
    """Add the law's own words after the first mention of each abbreviation.

    Idempotent: an expansion already present in the query is not added again, so a
    query that already says "curricular practical training" is left alone.
    """
    out = query
    for term, rx in _COMPILED:
        if term.expansion.lower() in out.lower():
            continue
        m = rx.search(out)
        if m:
            out = f"{out[: m.end()]} ({term.expansion}){out[m.end() :]}"
    return out


# Sibling classifications whose regulations share their wording. EB-1A (immigrant) and
# O-1 (nonimmigrant) are both written as "extraordinary ability in the sciences, arts,
# education, business, or athletics", and both list near-identical evidentiary criteria
# — so no choice of words separates them, and none did: measured with no expansion and
# with three different ones, O-1 provisions took 4-7 of 12 slots on every EB-1A
# question, and 204.5(h)'s "at least three" rule never ranked above O-1's. A question
# that names one of them and not the other is not asking about the other, so the
# other's provisions are set aside. A question naming both keeps both.
#
# F-1 and M-1 are the same trap. With the F-1 fixed-period rule enjoined and withheld,
# the only "fixed period of admission" text left in an F-1 PhD student's slate was M-1's
# 8 CFR 214.2(m), and the model told them: "Your admission is limited to a period of 3
# years ... as stated in [2] 8 CFR § 214.2(m)". The Policy Manual covers F and M in the
# same chapters, so only sections whose own heading is M-only are set aside; mixed
# sections ("An F-1 or M-1 student ...") stay.
#
# Each provision is identified by its own heading, checked against the corpus in
# tests/test_terminology.py; the one heading not in the corpus is quoted from its URL.


@dataclass(frozen=True)
class Scope:
    name: str
    pattern: str  # how the classification is recognised in a question
    provisions: tuple[tuple[str, str], ...]  # (citation prefix, its heading, verbatim)
    source: str
    group: str  # scopes are siblings only within a group


SCOPES: tuple[Scope, ...] = (
    Scope(
        "EB-1A",
        r"\bEB-?1A\b",
        (
            ("8 CFR § 204.5(h)", "Aliens with extraordinary ability"),
            ("USCIS PM Vol 6, Pt F, Ch 2", "Chapter 2 - Extraordinary Ability"),
        ),
        # "the EB-1A extraordinary ability immigrant visa classification"
        STEM_PATHWAYS,
        "extraordinary ability",
    ),
    Scope(
        "O-1",
        r"\bO-?1[AB]?\b",
        (
            ("8 CFR § 214.2(o)", "Aliens of extraordinary ability or achievement"),
            # Every chapter of Part M is about O classification; the part's own title
            # is not in the corpus.
            (
                "USCIS PM Vol 2, Pt M",
                "Part M - Nonimmigrants of Extraordinary Ability or Achievement (O)",
            ),
        ),
        "https://www.uscis.gov/policy-manual/volume-2-part-m",
        "extraordinary ability",
    ),
)

_M1_ONLY_PM = (  # every Part F section headed M-1 or M-2 alone, read from the corpus
    ("Ch 2, B.2", "2. M-1 Students"),
    ("Ch 3, A.2", "2. M-1 Students"),
    ("Ch 3, B.2", "2. M-1 Students"),
    ("Ch 3, E.2", "2. M-1 Students"),
    ("Ch 3, F.2", "2. M-1 Students"),
    ("Ch 4, B", "B. M-1 Students"),
    ("Ch 5, E", "E. M-1 Practical Training"),
    ("Ch 7, B", "B. M-1 Students"),
    ("Ch 8, C.2", "2. M-1 Students"),
    ("Ch 8, F.2", "2. M-1 Students"),
    ("Ch 9, D.2", "2. M-2 Dependents"),
)

SCOPES += (
    Scope(
        "F-1",
        r"\bF-?1\b",
        (
            (
                "8 CFR § 214.2(f)",
                "Students in colleges, universities, seminaries, conservatories, "
                "academic high schools, elementary schools",
            ),
        ),
        "8 CFR 214.2(f), heading",
        "student",
    ),
    Scope(
        "M-1",
        r"\bM-?1\b",
        (
            (
                "8 CFR § 214.2(m)",
                "Students in established vocational or other recognized nonacademic "
                "institutions",
            ),
            *((f"USCIS PM Vol 2, Pt F, {sec}", head) for sec, head in _M1_ONLY_PM),
        ),
        "8 CFR 214.2(m), heading; Policy Manual Vol 2, Part F section headings",
        "student",
    ),
)

_SCOPE_RX = tuple((s, re.compile(s.pattern, re.IGNORECASE)) for s in SCOPES)


def under(citation: str, prefix: str) -> bool:
    """True if `citation` is `prefix` or one of its parts. "Ch 2" is not "Ch 20", and
    "Ch 4, B" is not "Ch 4, BB" — but it is "Ch 4, B.1"."""
    return citation == prefix or citation.startswith(
        (f"{prefix},", f"{prefix}(", f"{prefix}.")
    )


def excluded(query: str) -> tuple[str, ...]:
    """Citation prefixes of sibling classifications the query did not ask about: within
    a group, if the query names some members, the others are set aside."""
    named = {s.name for s, rx in _SCOPE_RX if rx.search(query)}
    groups = {s.group for s in SCOPES if s.name in named}
    return tuple(
        p
        for s in SCOPES
        if s.group in groups and s.name not in named
        for p, _ in s.provisions
    )
