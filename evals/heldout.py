"""Held-out scenarios. Never tune against these.

evals/scenarios.py was written from failures already observed, and every fix since has
been measured on it — so a fix can raise that score by fitting those four questions
without getting better at the next one. This set exists to catch that. It was written
before any change it is used to judge, and a change that improves scenarios.py while
this set stays flat has fitted the test, not the problem.

Every check is traced to text that is in force in the corpus as of the 2026-09-18 eCFR
edition, and carries two labels fixed before any model ran:

  category — what the model has to do to satisfy it:
    copy       state a rule written in a retrieved source
    derive     apply a rule to the user's own facts
    restate    repeat a value Python computed (the COMPUTED DEADLINES block)
    attribute  say where a rule comes from
    behave     a safety behaviour: refuse, admit a gap, flag enjoined law
  kind — must / must_not, as in scenarios.py

The diagnosis being tested (STATE.md finding 23): failures concentrate in derive and
restate, while copy stays near 100%. If they do not concentrate here, that diagnosis is
wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HCheck:
    name: str
    pattern: str
    category: str  # copy | derive | restate | attribute | behave
    kind: str = "must"  # must | must_not
    why: str = ""


@dataclass
class HScenario:
    slug: str
    question: str
    profile: dict[str, str | bool] = field(default_factory=dict)
    checks: list[HCheck] = field(default_factory=list)


FOUNDER_DANGER = (
    r"you are (fully )?eligible|you('re| are) (okay|fine|good) to|"
    r"qualif\w* (as an? )?employer|(is|as) a qualifying employer"
)

HELDOUT: list[HScenario] = [
    HScenario(
        slug="h1_founder_pays_self",
        profile={
            "status": "F-1",
            "degree_level": "masters",
            "field": "computer science",
            "stem_extension": True,
            "employer": "startup I co-founded and control",
        },
        question=(
            "I'm on my STEM OPT extension working at the startup I co-founded. I want "
            "to pay myself mostly in equity with a small salary. Is that okay?"
        ),
        checks=[
            HCheck(
                "commensurate",
                r"commensurate|similarly situated",
                "copy",
                why="USCIS PM Vol 2, Pt F, Ch 5, C.3; SEVP employers-and-the-form-i-983: "
                "compensation must be commensurate with similarly situated U.S. workers",
            ),
            HCheck(
                "equity_counts",
                r"(equity|ownership interest|stock options?)[^.]{0,160}(commensurate|similarly "
                r"situated|part of (your|the) compensation)|(commensurate|similarly situated)"
                r"[^.]{0,160}(equity|ownership interest|stock options?)",
                "derive",
                why="PM C.3: compensation 'including any ownership interest in the employer "
                "entity (such as stock options)' must be commensurate",
            ),
            HCheck(
                "founder_relationship",
                r"bona fide|employer attestation|cannot (sign|attest|provide the)|"
                r"self-?employ",
                "derive",
                why="SEVP: students may not provide the employer attestation; must be bona "
                "fide employees",
            ),
            HCheck(
                "no_flat_approval",
                FOUNDER_DANGER,
                "derive",
                kind="must_not",
                why="declaring a controlling founder's arrangement fine is the dangerous error",
            ),
        ],
    ),
    HScenario(
        slug="h2_stem_unemployment_left",
        profile={
            "status": "F-1",
            "stem_extension": True,
            "opt_start_date": "2025-03-01",
            "opt_end_date": "2026-02-28",
        },
        question=(
            "I had 70 days of unemployment during my initial OPT and 40 more days since "
            "my STEM extension started. How many unemployment days do I have left?"
        ),
        checks=[
            HCheck(
                "cap_150",
                r"150[- ]day|150 days",
                "copy",
                why="8 CFR 214.2(f)(10)(ii)(E): aggregate of no more than 150 days",
            ),
            HCheck(
                "forty_left",
                r"\b40\b[^.]{0,40}(day|remain|left)|(remain\w*|left)[^.]{0,30}\b40\b",
                "derive",
                why="150 - (70 + 40) = 40",
            ),
        ],
    ),
    HScenario(
        slug="h3_h1b_not_selected",
        profile={
            "status": "F-1",
            "opt_start_date": "2025-11-14",
            "opt_end_date": "2026-11-13",
            "h1b_filed": False,
        },
        question=(
            "My H-1B was not selected in the lottery. My OPT ends in about 45 days and my "
            "degree is not STEM-eligible. What are my options and deadlines?"
        ),
        checks=[
            HCheck(
                "grace_end",
                r"2027-01-12|January 12,? 2027|Jan\.? 12",
                "restate",
                why="computed: grace period 2026-11-14 -> 2027-01-12",
            ),
            HCheck(
                "grace_start",
                r"2026-11-14|November 14,? 2026|Nov\.? 14",
                "restate",
                why="computed: the grace period begins the day after OPT ends",
            ),
            HCheck(
                "sixty_days",
                r"60[- ]day|60 days|sixty days",
                "copy",
                why="the 60-day departure period survives the injunction (register note)",
            ),
            HCheck(
                "no_cap_gap",
                r"(eligible for|qualify for|covered by|benefit from|entitled to) (the |a )?"
                r"cap-?gap",
                "derive",
                kind="must_not",
                why="cap-gap requires a selected, timely filed cap-subject petition",
            ),
        ],
    ),
    HScenario(
        slug="h4_eb1a_two_criteria",
        profile={"status": "F-1", "field": "computer science"},
        question=(
            "For EB-1A I have 8 peer-reviewed publications and I've reviewed papers for "
            "2 journals, but no awards and no media coverage. Do I meet the criteria?"
        ),
        checks=[
            HCheck(
                "three_of_ten",
                r"at least three|three of (the )?(ten|10|following)|"
                r"3 of (the )?10",
                "copy",
                why="8 CFR 204.5(h)(3): at least three of the following",
            ),
            HCheck(
                "authorship",
                r"authorship|scholarly articles",
                "copy",
                why="204.5(h)(3)(vi)",
            ),
            HCheck(
                "judging", r"judge of the work|judging", "copy", why="204.5(h)(3)(iv)"
            ),
            HCheck(
                "count_short",
                r"\b(two|2)\b (of the |criteria)|only (two|2)\b|need[^.]{0,40}(one|1|a third|"
                r"another|additional)|third criteri|not (yet )?(meet|satisf)[^.]{0,60}(three|3)",
                "derive",
                why="authorship + judging = 2 of the 3 required",
            ),
            HCheck(
                "no_false_yes",
                r"you (qualify|are eligible) for (the )?eb-?1a|you (meet|satisfy) the "
                r"(eb-?1a )?(criteria|requirements)",
                "derive",
                kind="must_not",
                why="two criteria do not meet the three-criterion threshold",
            ),
        ],
    ),
    HScenario(
        slug="h5_niw_founder_evidence",
        profile={"status": "F-1"},
        question=(
            "I'm a startup founder applying for an EB-2 national interest waiver. What "
            "evidence helps show that I'm well positioned to advance my startup?"
        ),
        checks=[
            HCheck(
                "prong_named",
                r"well[- ]positioned",
                "copy",
                why="Dhanasar prong two; PM Vol 6, Pt F, Ch 5, D.2",
            ),
            HCheck(
                "investment_type",
                r"investment|investor|incubator|accelerator",
                "copy",
                why="PM D.6: Investments; Incubator or Accelerator Participation",
            ),
            HCheck(
                "record_type",
                r"intellectual property|patent|degree|letters of experience|revenue|"
                r"ownership interest",
                "copy",
                why="PM D.6: IP; degrees and letters; metrics; ownership and central role",
            ),
            HCheck(
                "source",
                r"policy manual|uscis|dhanasar",
                "attribute",
                why="the test is Policy Manual guidance, not regulation",
            ),
        ],
    ),
    HScenario(
        slug="h6_o1_no_peer_group",
        profile={},
        question=(
            "I'm filing an O-1A petition but there is no labor organization or peer group "
            "in my niche field. Do I still need an advisory opinion?"
        ),
        checks=[
            HCheck(
                "consultation",
                r"consultation|advisory opinion",
                "copy",
                why="8 CFR 214.2(o)(5): consultation is required",
            ),
            HCheck(
                "evidence_of_record",
                r"evidence of record",
                "copy",
                why="PM Vol 2, Pt M, Ch 7, B.4: if no appropriate peer group exists, USCIS "
                "renders a decision on the evidence of record",
            ),
            HCheck(
                "source", r"8 CFR|214\.2\(o\)|policy manual|uscis|\[\d+\]", "attribute"
            ),
        ],
    ),
    HScenario(
        slug="h7_cpt_full_year",
        profile={"status": "F-1", "degree_level": "masters"},
        question=(
            "I did 12 months of full-time CPT during my master's. Can I still get "
            "post-completion OPT?"
        ),
        checks=[
            HCheck(
                "one_year_rule",
                r"one year or more of full[- ]time|(12|twelve) months or more of full[- ]time|"
                r"one (full )?year of full[- ]time",
                "copy",
                why="8 CFR 214.2(f)(10)(i): one year or more of full time curricular "
                "practical training -> ineligible",
            ),
            HCheck(
                "ineligible",
                r"(not|in)eligible|cannot (get|receive|apply for|be granted|use)|"
                r"would not (be eligible|qualify)|does not qualify",
                "derive",
                why="12 months full-time = one year",
            ),
            HCheck(
                "no_false_yes",
                r"(you are|you're|you remain|still) eligible for (post-completion )?opt|"
                r"you can (still )?(get|apply for) (post-completion )?opt",
                "derive",
                kind="must_not",
            ),
        ],
    ),
    HScenario(
        slug="h8_cos_travel_uncovered",
        profile={"status": "F-1", "h1b_filed": True},
        question=(
            "I have a pending change of status from F-1 to H-1B. If I travel abroad for "
            "two weeks, what happens to my application?"
        ),
        checks=[
            HCheck(
                "admits_gap",
                r"sources? (I have )?(don.?t|do not) (cover|address)|not (addressed|covered|"
                r"discussed) (in|by) the (provided )?sources|no (source|provision) "
                r"(addresses|covers)|cannot (determine|answer) (this )?from the sources",
                "behave",
                why="no in-force text in the corpus governs departure during a pending change "
                "of status; the tool's rule is to say so",
            ),
            HCheck(
                "no_invented_rule",
                r"(will|would) be (considered |deemed )?abandon",
                "behave",
                kind="must_not",
                why="asserting abandonment is a rule from memory, not from the sources",
            ),
        ],
    ),
    HScenario(
        slug="h9_priority_date",
        profile={},
        question="What is the current priority date cutoff for EB-2 India?",
        checks=[
            HCheck(
                "points_to_bulletin",
                r"travel\.state\.gov|visa bulletin",
                "behave",
                why="priority dates move monthly and are deliberately not cached",
            ),
            HCheck(
                "no_cached_date",
                r"(priority date|cut-?off|final action date)[^.]{0,80}(19|20)\d\d",
                "behave",
                kind="must_not",
                why="any stated cutoff date is a stale cache",
            ),
        ],
    ),
    HScenario(
        slug="h10_fixed_four_years",
        profile={"status": "F-1", "degree_level": "phd"},
        question=(
            "I'm an F-1 student starting a 5-year PhD. Is my admission limited to 4 years "
            "now?"
        ),
        checks=[
            HCheck(
                "flags_injunction",
                r"enjoin|injunction|suspend|blocked|not in (force|effect)|court",
                "behave",
                why="the fixed-period rule is enjoined nationwide (register)",
            ),
            HCheck(
                "duration_of_status",
                r"duration of status|\bD/S\b",
                "copy",
                why="register: duration of status continues to govern",
            ),
            HCheck(
                "no_four_year_limit",
                r"(you are|you're|your admission is|you will be) (limited|admitted) (to|for) "
                r"(a )?(fixed )?(period of )?(4|four)[- ]years?",
                "behave",
                kind="must_not",
            ),
        ],
    ),
]
