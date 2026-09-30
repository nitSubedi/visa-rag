"""The Visa Bulletin: parsed from the page, compared in code, stamped with its month."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from visa import bulletin as vb
from visa import rules

FIXTURE = Path(__file__).parent / "fixtures" / "visa-bulletin-for-july-2026.html"


@pytest.fixture(scope="module")
def july() -> dict[str, object]:
    return vb.parse_html(FIXTURE.read_text(errors="ignore"))


def test_the_month_comes_from_the_page(july) -> None:
    assert july["month"] == "2026-07"


@pytest.mark.parametrize(
    ("chart", "cat", "area", "want"),
    [
        # read off the July 2026 page by eye, cell by cell
        ("final_action", "EB-1", "china", "2023-06-01"),
        ("final_action", "EB-2", "india", "U"),
        ("final_action", "EB-2", "all", "C"),
        ("final_action", "EB-3", "india", "2014-01-01"),
        ("dates_for_filing", "EB-2", "india", "2015-01-15"),
        ("dates_for_filing", "EB-3", "philippines", "2024-01-01"),
    ],
)
def test_cells_land_in_the_right_place(july, chart, cat, area, want) -> None:
    assert july[chart][cat][area] == want


@pytest.mark.parametrize(
    ("pd", "cutoff", "want"),
    [
        ("2021-08-31", "2021-09-01", "CURRENT"),  # earlier than the cut-off
        ("2021-09-01", "2021-09-01", "NOT CURRENT"),  # "earlier than", so not on the day
        ("2030-01-01", "C", "CURRENT"),
        ("2000-01-01", "U", "UNAVAILABLE"),
    ],
)
def test_the_bulletins_own_comparison(pd, cutoff, want) -> None:
    assert vb.compare(dt.date.fromisoformat(pd), cutoff) == want


def _loaded(monkeypatch, july, month: str = "2026-07", chart: str | None = None) -> None:
    b = vb.Bulletin(
        month=dt.date.fromisoformat(month + "-01"),
        charts={c: july[c] for c in vb.CHARTS},
        uscis_chart=chart,
        sources=(),
        method="test",
        uscis_url="",
    )
    monkeypatch.setattr(vb, "load", lambda: b)


def _decide(q: str, prof: dict[str, object] | None = None):
    facts = {**(prof or {}), **rules.read_situation(q)}
    (r,) = [x for x in rules.evaluate(facts) if x.rule == "priority_date_current"]
    return r


def test_no_bulletin_means_no_date(monkeypatch) -> None:
    monkeypatch.setattr(vb, "load", lambda: None)
    o = _decide("What is the current priority date cutoff for EB-2 India?")
    assert isinstance(o, rules.Outcome) and o.decision.startswith("NO VISA BULLETIN")


def test_an_old_bulletin_says_so_in_the_decision(monkeypatch, july) -> None:
    _loaded(monkeypatch, july, chart="final_action")
    o = _decide(
        "My EB-2 priority date is 2014-06-01. Is it current?",
        {"country_of_birth": "India"},
    )
    assert isinstance(o, rules.Outcome)
    assert o.decision.startswith("THIS IS THE JULY 2026 BULLETIN, NOT THIS MONTH'S")
    assert "Final Action Dates: UNAVAILABLE" in o.decision
    assert "Dates for Filing: CURRENT (cut-off 2015-01-15)" in o.decision
    assert "USCIS accepts the Final Action Dates chart" in o.decision


@pytest.mark.parametrize(
    ("prof", "fact"),
    [
        ({}, "eb_category"),
        ({"eb_category": "EB-3"}, "country_of_birth"),
        ({"eb_category": "EB-3", "country_of_birth": "Nigeria"}, "priority_date"),
    ],
)
def test_missing_facts_are_asked_in_order(monkeypatch, july, prof, fact) -> None:
    _loaded(monkeypatch, july)
    n = _decide("Is my priority date current?", prof)
    assert isinstance(n, rules.Needs) and n.fact == fact


@pytest.mark.parametrize(
    ("country", "area"),
    [
        ("India", "india"),
        ("born in mainland China", "china"),
        ("Hong Kong", "all"),
        ("Taiwan", "all"),
        ("the Philippines", "philippines"),
        ("Nigeria", "all"),
    ],
)
def test_chargeability_by_country_of_birth(country, area) -> None:
    assert rules.area_of(country) == area


@pytest.mark.parametrize(
    ("reply", "fact", "want"),
    [
        ("EB-2 NIW", "eb_category", "EB-2"),
        ("EB1A", "eb_category", "EB-1"),
        ("March 15, 2021", "priority_date", "2021-03-15"),
        ("2021-03-15", "priority_date", "2021-03-15"),
        ("sometime in 2021", "priority_date", None),
    ],
)
def test_follow_up_replies_are_parsed_by_code(reply, fact, want) -> None:
    got = rules.apply_answer(rules.Needs("priority_date_current", fact, "?"), reply)
    assert got.get(fact) == want


def test_the_shipped_table_says_where_it_came_from() -> None:
    """Either empty, or stamped with its month, its method and at least two sources."""
    import tomllib

    shipped = Path(__file__).parents[1] / "bulletin" / "visa_bulletin.toml"
    d = tomllib.loads(shipped.read_text())
    if d["month"]:
        assert d["method"] and len(d["sources"]) >= 2


def test_a_republisher_format_reads_the_same() -> None:
    """Envoy prints "July 1, 2024" and "Current", with a Notes column."""
    rows = [
        ["Category", "All Others", "China", "India", "Notes"],
        ["EB-1", "Current", "July 1, 2024", "July 1, 2024", "China and India advance."],
        ["EB-3 Other Workers", "June 1, 2022", "October 1, 2020", "January 15, 2015", ""],
    ]
    t = vb.read_employment(rows, strict=False)
    assert t["EB-1"] == {"all": "C", "china": "2024-07-01", "india": "2024-07-01"}
    assert t["EB-3 other workers"]["china"] == "2020-10-01"


def test_a_typo_cell_drops_out_instead_of_voting() -> None:
    rows = [
        ["Employment-based", "All Chargeability", "INDIA"],
        ["2nd", "15MAR26", "15MAY"],
    ]
    assert vb.read_employment(rows, strict=False) == {"EB-2": {"all": "2026-03-15"}}
    with pytest.raises(ValueError):
        vb.read_employment(rows, strict=True)  # the official page must parse fully


def test_consensus_needs_two_agreeing_and_no_dissent() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    from bulletin_update import consensus

    a = {"final_action": {"EB-2": {"all": "2025-01-01", "india": "2013-11-01"}}}
    b = {"final_action": {"EB-2": {"all": "2025-01-01", "india": "2013-12-01"}}}
    c = {"final_action": {"EB-2": {"all": "2025-01-01", "china": "2021-10-01"}}}
    agreed, notes = consensus({"A": a, "B": b, "C": c})
    assert agreed["final_action"]["EB-2"] == {"all": "2025-01-01"}  # 3 agree
    assert any("DISAGREE" in n and "india" in n for n in notes)  # A vs B: withheld
    assert any("single source" in n and "china" in n for n in notes)  # only C: withheld


@pytest.mark.parametrize(
    ("month", "today", "want"),
    [
        ("2026-10", "2026-09-30", "upcoming"),  # published ahead: about to apply
        ("2026-10", "2026-10-15", "current"),
        ("2026-07", "2026-10-01", "stale"),
    ],
)
def test_next_months_bulletin_is_upcoming_not_stale(month, today, want) -> None:
    b = vb.Bulletin(dt.date.fromisoformat(month + "-01"), {}, None, (), "", "")
    assert b.timing(dt.date.fromisoformat(today)) == want
