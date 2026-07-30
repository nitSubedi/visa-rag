"""Scored scenarios for situational reasoning.

Each check is a regex over the generated answer. `must` = the answer is wrong or
dangerously incomplete without it. `must_not` = an assertion observed to be false.

These come from real observed failures, not imagined ones: the 2026-07-29 run of
`stem_self_employment` claimed there is no prohibition on OPT unemployment (there is a
90-day cap), said STEM OPT needs "30+ hours per week" (the floor is 20), and never
mentioned E-Verify despite it being in both the corpus and the injected deadlines.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Check:
    name: str
    pattern: str
    kind: str = "must"  # must | must_not
    why: str = ""


@dataclass
class Scenario:
    slug: str
    question: str
    profile: dict[str, str | bool] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)


SCENARIOS: list[Scenario] = [
    Scenario(
        slug="stem_self_employment",
        profile={
            "status": "F-1",
            "country_of_birth": "India",
            "degree_level": "masters",
            "field": "computer science",
            "program_end_date": "2026-02-28",
            "opt_start_date": "2026-03-01",
            "opt_end_date": "2027-02-28",
            "stem_extension": False,
            "employer": "own startup (pre-revenue, 3 people)",
        },
        question=(
            "I was unemployed for the first 60 days of my OPT. Since May I've been "
            "working about 20 hours a week at my own startup, which I co-founded and "
            "control. I want to apply for the STEM extension. Am I okay?"
        ),
        checks=[
            Check(
                "unemployment_cap",
                r"90[- ]day|90 days|aggregate.{0,30}unemploy",
                why="60 of 90 days already used — the binding constraint",
            ),
            Check(
                "unemployment_remaining",
                r"\b30\b.{0,40}(day|remain)|remain\w*.{0,20}\b30\b",
                why="should quantify what is left, not just cite the cap",
            ),
            Check("everify", r"e-?verify", why="gating requirement for STEM OPT"),
            Check(
                "self_employment",
                r"self-?employ|own company|control.{0,30}(company|entity)|"
                r"employer-employee",
                why="student controls the employer — the dispositive issue",
            ),
            Check(
                "hours_floor_wrong",
                r"30\+?\s*hours|thirty hours|full[- ]time.{0,25}30",
                kind="must_not",
                why="the floor is 20 hours/week, not 30",
            ),
            Check(
                "no_unemployment_rule",
                r"do(es)? not (explicitly )?prohibit unemploy|"
                r"no (explicit )?(limit|prohibition) on unemploy",
                kind="must_not",
                why="flatly false; there is a hard 90-day cap",
            ),
        ],
    ),
    Scenario(
        slug="niw_standard",
        question="What is the legal standard for an EB-2 national interest waiver?",
        checks=[
            Check("prong_merit", r"substantial merit"),
            Check("prong_positioned", r"well positioned"),
            Check(
                "prong_balance", r"on balance|beneficial to (the )?(united states|waive)"
            ),
            Check(
                "source_is_policy",
                r"dhanasar|policy manual|USCIS",
                why="the test is not in the regulation; say where it comes from",
            ),
        ],
    ),
    Scenario(
        slug="opt_filing_window",
        profile={"status": "F-1", "program_end_date": "2027-05-14"},
        question="When exactly can I file my I-765 for post-completion OPT?",
        checks=[
            Check("opens", r"2027-02-13|February 13, 2027|Feb(ruary)? 13"),
            Check("closes", r"2027-07-13|July 13, 2027|Jul(y)? 13"),
            Check(
                "dso_30_days",
                r"30 days.{0,40}(DSO|recommendation)|"
                r"(DSO|recommendation).{0,40}30 days",
            ),
        ],
    ),
    Scenario(
        slug="travel_on_opt",
        profile={
            "status": "F-1",
            "opt_start_date": "2026-03-01",
            "opt_end_date": "2027-02-28",
        },
        question=(
            "My H-1B was selected in the lottery and my employer filed a cap-subject "
            "petition requesting change of status. My OPT ends before October 1. "
            "What happens in the gap?"
        ),
        checks=[
            Check("cap_gap", r"cap-?gap"),
            Check("sept_30", r"September 30|Sep(t)?\.? 30|9/30"),
            Check("timely_filed", r"timely|pending or approved|while in.{0,30}status"),
        ],
    ),
]
