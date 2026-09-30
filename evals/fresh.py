"""A second held-out set, in the v0.1 scope (F-1 / OPT / STEM OPT / cap-gap).

Written 2026-09-29, after the model bake-off and before any model saw these questions,
to test the chosen model on questions nothing was tuned against — heldout.py has been
read closely all day, and a model or prompt can be fitted to sixteen questions without
being better at the seventeenth. Every "must" fact below was confirmed in the served
(post-redaction) corpus before the check was written; every "not in the corpus" claim
was confirmed absent. Never tune against this set.

Same labels as heldout.py: category copy / derive / restate / attribute / behave.
"""

from __future__ import annotations

from heldout import GAP, HCheck, HScenario

FRESH: list[HScenario] = [
    HScenario(
        slug="f1_precompletion_deducted",
        profile={"status": "F-1", "degree_level": "masters"},
        question=(
            "I used 6 months of full-time pre-completion OPT during my master's. How "
            "much post-completion OPT can I still get?"
        ),
        checks=[
            HCheck(
                "deducted",
                r"deduct|subtract|reduc",
                "copy",
                why="PM Vol 2, Pt F, Ch 5, C: pre-completion time at the same level is "
                "deducted from post-completion OPT",
            ),
            HCheck(
                "six_left",
                r"\b(6|six) months",
                "derive",
                why="12 - 6 full-time months = 6",
            ),
            HCheck(
                "no_full_twelve",
                r"(still|can) (get|receive|have)[^.]{0,30}(12|twelve) months",
                "derive",
                kind="must_not",
            ),
        ],
    ),
    HScenario(
        slug="f2_stem_filing_window",
        profile={"status": "F-1", "field": "computer science"},
        question="When can I file my STEM OPT extension application?",
        checks=[
            HCheck(
                "ninety_before",
                r"90 days before",
                "copy",
                why="PM C.3/C.5: up to 90 days before the current OPT EAD expires",
            ),
            HCheck(
                "sixty_after_dso",
                r"60 days (after|of)[^.]{0,40}(DSO|recommendation)",
                "copy",
                why="PM C.3/C.5: no more than 60 days after the DSO enters the recommendation",
            ),
        ],
    ),
    HScenario(
        slug="f3_stem_pending_work",
        profile={"status": "F-1", "stem_extension": True},
        question=(
            "I filed my STEM OPT extension on time, but my OPT EAD expired last week and "
            "USCIS hasn't decided yet. Can I keep working?"
        ),
        checks=[
            HCheck(
                "one_eighty",
                r"180 days",
                "copy",
                why="PM C.5: may continue working up to 180 days after OPT expires or until "
                "the decision, whichever is earlier",
            ),
            HCheck(
                "yes_continue",
                r"(can|may) (continue|keep) working|yes\b",
                "derive",
            ),
        ],
    ),
    HScenario(
        slug="f4_cap_gap_selected",
        profile={"status": "F-1", "h1b_filed": True},
        question=(
            "My H-1B cap petition was selected and filed on time, but my OPT ends in June. "
            "Can I keep working until my H-1B starts?"
        ),
        checks=[
            HCheck(
                "october_first",
                r"October 1|Oct\.? 1\b|09-30|September 30|Sept\.? 30",
                "copy",
                why="PM D.1: the cap-gap extension continues until October 1",
            ),
            HCheck("cap_gap", r"cap[- ]gap", "copy", why="PM D.1"),
        ],
    ),
    HScenario(
        slug="f5_no_everify",
        profile={"status": "F-1", "field": "data science"},
        question=(
            "My employer isn't enrolled in E-Verify. Can I still get the STEM OPT "
            "extension with them?"
        ),
        checks=[
            HCheck(
                "everify_required",
                r"good standing|must (be )?(enroll|participat|use)|E-Verify (participant|"
                r"employer)",
                "copy",
                why="8 CFR 214.2(f)(10)(ii)(C); PM C.3: employer must remain a participant "
                "in good standing with E-Verify",
            ),
            HCheck(
                "no",
                r"\bno\b|cannot|can't|not (be )?eligible|would not qualify",
                "derive",
            ),
        ],
    ),
    HScenario(
        slug="f6_new_employer_i983",
        profile={"status": "F-1", "stem_extension": True},
        question=(
            "I'm on STEM OPT and just switched employers. How long do I have to submit a "
            "new training plan?"
        ),
        checks=[
            HCheck(
                "ten_days",
                r"\b10 days|ten days",
                "copy",
                why="PM C.4: submit a new Form I-983 within 10 days of beginning a new "
                "practical training opportunity with a new employer",
            ),
            HCheck("i983", r"I-983", "copy"),
        ],
    ),
    HScenario(
        slug="f7_opt_start_too_late",
        profile={"status": "F-1", "program_end_date": "2027-05-15"},
        question=(
            "My program ends in May 2027. Can I request an OPT start date in September "
            "2027?"
        ),
        checks=[
            HCheck(
                "sixty_after_end",
                r"60 days after",
                "copy",
                why="PM C.2: may not request a start date more than 60 days after the "
                "program end date",
            ),
            HCheck(
                "no",
                r"\bno\b|cannot|can't|not (be )?(able|allowed|permitted)|too late",
                "derive",
                why="September is more than 60 days after a mid-May end date",
            ),
            HCheck(
                "no_false_yes",
                r"\byes\b[^.]{0,20}(you can|request)|you can request (a|an OPT) start date "
                r"in September",
                "derive",
                kind="must_not",
            ),
        ],
    ),
    HScenario(
        slug="f8_abroad_no_extension",
        profile={"status": "F-1", "opt_start_date": "2026-06-01", "opt_end_date": "2027-05-31"},
        question=(
            "If I spend two months abroad during my OPT, does my OPT end date move back "
            "by two months?"
        ),
        checks=[
            HCheck(
                "no_extension",
                r"does not extend|doesn.t extend|will not (be )?extend|won.t extend|does not "
                r"move|no\b",
                "copy",
                why="PM Vol 2, Pt F, Ch 7, A: time outside the U.S. does not extend OPT",
            ),
            HCheck(
                "no_false_yes",
                r"(will|would) (be )?(extended|move back|shift)|moves back by two months",
                "derive",
                kind="must_not",
            ),
        ],
    ),
    HScenario(
        slug="f9_unemployment_regular_opt",
        profile={"status": "F-1", "stem_extension": False, "opt_start_date": "2026-06-01"},
        question=(
            "I've been unemployed for 25 days on my regular OPT. How many unemployment days "
            "do I have left?"
        ),
        checks=[
            HCheck("ninety", r"\b90[- ]day|\b90 days", "copy", why="214.2(f)(10)(ii)(E)"),
            HCheck(
                "sixty_five", r"\b65\b", "derive", why="90 - 25 = 65 (rule table computes it)"
            ),
        ],
    ),
    HScenario(
        slug="f10_cpt_under_a_year",
        profile={"status": "F-1", "degree_level": "masters"},
        question=(
            "I did 10 months of full-time CPT and 4 months of part-time CPT during my "
            "master's. Can I still do OPT?"
        ),
        checks=[
            HCheck(
                "year_rule",
                r"one year|1 year|12 months",
                "copy",
                why="214.2(f)(10)(i); PM Ch 5, B: one year or more of FULL-TIME CPT bars OPT",
            ),
            HCheck(
                "not_barred",
                r"not barred|(still )?eligible|can (still )?(do|get|apply)|yes\b",
                "derive",
                why="10 full-time months < 12; part-time CPT does not count",
            ),
            HCheck(
                "no_false_bar",
                r"(you are|you're) (ineligible|not eligible)|cannot (do|get) OPT",
                "derive",
                kind="must_not",
            ),
        ],
    ),
    HScenario(
        slug="f11_austin_sponsors",
        profile={"status": "F-1"},
        question="Which employers in Austin sponsor H-1B visas?",
        checks=[
            HCheck(
                "admits_gap",
                GAP,
                "behave",
                why="no employer or sponsorship lists in the served corpus",
            ),
            HCheck(
                "no_named_employers",
                r"\b(Dell|Apple|Tesla|Google|Amazon|IBM|Oracle|Samsung|Meta|Indeed)\b",
                "behave",
                kind="must_not",
            ),
        ],
    ),
    HScenario(
        slug="f12_stem_mba_schools",
        profile={"status": "F-1"},
        question="Which universities offer STEM-designated MBA programs?",
        checks=[
            HCheck(
                "admits_gap",
                GAP,
                "behave",
                why="no school or program lists in the served corpus",
            ),
            HCheck(
                "no_named_schools",
                r"\b(MIT|Stanford|Harvard|Carnegie|Purdue|Wharton|Yale|Columbia|NYU)\b",
                "behave",
                kind="must_not",
            ),
        ],
    ),
]
