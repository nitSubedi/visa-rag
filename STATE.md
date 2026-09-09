# Project state — visa-rag

**Last updated:** 2026-09-09
**Status:** v1 working end-to-end, plus stages 1-3 of the reasoning/dialogue spec.
Stage 4 (persisted situation model) and v2 (sharing/packaging) not started.

---

## Where things stand

`visa init` → `visa ask` works, and the REPL now holds a conversation. **8,305 chunks**
indexed across four tiers, all five sources ingesting cleanly. 92 tests passing.

```
8-cfr                 4,247 chunks   tier 2 regulation   (edition 2026-08-21)
uscis-policy-manual   2,411 chunks   tier 3 policy       (footnotes stripped, see 16)
ina-8usc              1,621 chunks   tier 1 statute
sevp-stem-opt            21 chunks   tier 4 guidance
uscis-policy-updates      5 chunks   tier 4 guidance
```

Reindexed 2026-08-25; all five shards inside their refresh windows as of 2026-09-09.

Verified live: asking for the NIW standard returns the correct *Dhanasar* three prongs,
cited to USCIS PM Vol 6, Pt F, Ch 5. Default model is now `qwen2.5:7b` — see finding 7.

**Evals re-run 2026-09-09**, the first measurement since the reasoning and dialogue
work. Two runs of the *same code, same corpus, one hour apart*:

```
                        --repeat 3          --repeat 5
stem_self_employment    4/6                 3/6      (+unemployment_cap missing)
niw_standard            4/4                 4/4
opt_filing_window       3/3                 3/3
travel_on_opt           2/3                 2/3
                       13/16  (81%)        12/16  (75%)
                        391s                520s
```

**The published 81% was not reproducible, and that was the headline** (fixed since — see
10e; the instrument is deterministic again and the current figure is 12/16, 75%). More samples moved
it *down*, which is what you expect when 13/16 was the optimistic tail of a
distribution rather than a point. The honest current statement is **12-13/16, ~75-81%,
±1 check**, and README's flat "81%" overstates it — see finding 0d for the mechanism and
Known gaps for the decision it forces.

Three further things to read off this, none of them the headline:

**The motivating failure is unfixed.** `self_employment` and `everify` are exactly what
the reasoning spec was written to fix (finding 12), and they miss in both runs. Stages
1-3 changed the behaviour of a real conversation; they did not move this scenario. At
`--repeat 5` `unemployment_cap` joins them, so 4 of that scenario's 6 checks are now
unstable.

**Composition has inverted since finding 7.** That finding recorded the same 13/16 as
STEM **6/6** with `travel_on_opt` **0/3** — "perfect on substance, misses only dates".
Both runs today are the mirror image: STEM 4/6 and 3/6, travel 2/3 and 2/3. The two date
checks that were failing now pass and the substance checks fail, so the argument finding
7 rests on — that 7b's weakness is precisely the one the architecture removes in Python
— no longer describes the system.

**Both scenarios are stochastic, provably.** `stem_self_employment` scored 4/6 while
flagging three checks and 3/6 while flagging four; `travel_on_opt` scored 2/3 while
flagging two, twice. None of those combinations is reachable if every repeat produced
the same answer — three deterministic failures score 3/6, not 4/6. At least some flagged
checks pass in some runs and fail in others.

Two more things to read off this rather than the headline. **The motivating failure is
unfixed:** `self_employment` and `everify` are exactly what the reasoning spec was
written to fix (finding 12), and they still miss. Stages 1-3 changed the behaviour of a
real conversation; they did not move this scenario. **And the runs are no longer
deterministic** — see finding 0d, which is the more consequential of the two.

---

## Hard-won findings — do not re-derive these

**0. The single biggest bug: prompt overflow silently ate the safety features.**
`num_ctx` was 8192 while 12 retrieved chunks plus the system prompt ran **8,596
tokens**. Generation keeps the *tail*, so the profile and COMPUTED DEADLINES blocks —
placed first — were dropped on every substantial query. This is why the "deterministic
dates" feature appeared to be ignored, why E-Verify was never mentioned despite sitting
in the injected note, and why the unemployment cap was contradicted.

It presented as a *reasoning* failure and was actually a *plumbing* failure. Scored
evals went **44% → 88%** from the fix alone, with no model change. Three parts:
raise `num_ctx` to 16384, budget sources against it in `build_prompt`, and move
profile/deadlines to the **tail** immediately before the question.

Guarded by `tests/test_reasoning.py::test_computed_deadlines_survive_a_huge_source_set`.
**If you change `top_k`, `num_ctx`, or chunk size, re-run the evals** — this failure is
silent by construction.

**0b. Evals must run at temperature 0.** At 0.15 the same scenario swung ±1 check
between runs, which is larger than most effects being measured. Two "regressions" were
chased before this was noticed. `evals/run.py --repeat N` averages.

**0d. Temperature 0 no longer makes a run deterministic — 0b is now only half true.**
`evals/run.py` pins `stream_chat(..., temperature=0.0)` for the final generation, and
that was sufficient when generation was the only model call. It is not any more:
`plan_issues()` runs inside `answer()` at **temperature 0.3**, so the issue list — and
therefore the retrieval slate the answer is written from — is resampled on every repeat.
The eval's temperature discipline covers the last stage of a two-stage pipeline.

Measured directly on 2026-09-09: the same configuration scored **13/16 at `--repeat 3`
and 12/16 at `--repeat 5`**, an hour apart, no code or corpus change between them. The
per-scenario tallies confirm the mechanism — `stem_self_employment` scored 4/6 while
flagging *three* checks, which is arithmetically unreachable if every repeat produced
the same answer (three deterministic failures score 3/6).

Note the direction: raising `--repeat` moved the score **down**. 13/16 was the
optimistic end of a distribution, not a point estimate, and it is the number the README
publishes.

Consequences, in order of importance:
1. **The noise floor is wider than finding 11 assumed.** A one-check delta was already
   called below the instrument's resolution; it is now below its *variance* too, and a
   16-check suite cannot resolve anything smaller than a couple of checks.
2. `--repeat` is no longer an optional averaging convenience. A single run is a sample.
   **Do not compare two configurations at `--repeat 1`.**
3. The claim "deterministic at temperature 0 (verified 3/3 identical)" in Known gaps
   dated from before issue planning existed and has been corrected.

The fix is a decision, not a bug: either pin `plan_issues` to temperature 0 for evals
(measures the pipeline as configured, loses the sampling that issue-spotting may
benefit from) or keep it and raise `--repeat` and report variance rather than a point
score. Deciding this comes *before* the next attempt at citation compliance, because
that work will be judged by this instrument.

**0c. Retrieval is question-shaped, not issue-shaped.** A described situation embeds
toward passages resembling what the person *said*; the provision that decides the case
is the one they didn't know to mention. `answer.plan_issues()` decomposes the facts into
3-6 issues and `Index.search_many()` interleaves one retrieval per issue. Note the merge
must retrieve a *full* slate per issue — an early version used `k/n` per issue and,
after cross-issue dedup, returned **fewer** sources than a single pass (7 chunks / 2
provisions vs 12 / 9).

**1. The Policy Manual tier is mandatory, not a nice-to-have.**
8 CFR 204.5(k)(4)(ii) is the *entire* regulatory text on the national interest waiver:
"may exempt the requirement of a job offer ... if exemption would be in the national
interest." It defines nothing. Grepped the full Title 8 XML — `Dhanasar`, "substantial
merit", "well positioned" are all **absent**. Same for EB-1A: the reg lists ten
criteria, but the *Kazarian* two-step (criteria count, then final merits determination)
is not there. Both live only in the Policy Manual.

**2. eCFR paragraph parsing is genuinely subtle — three implementations got it wrong.**
Section 214.2 is 698k chars, 1,957 `<P>` elements. Top-level paragraphs are `(a)`,
`(b)`, `(c)`… but sub-paragraphs use roman numerals, so `(i)`, `(v)`, `(x)` are
indistinguishable from letters.

- Naive regex → text from F-1 `(f)` cited as `214.2(i)` (*information media*). Wrong
  citations are worse than no answer.
- Sequence-with-lookahead → still broke: `(i)` is exactly 3 after `(f)`.
- Strict next-letter → H-1B `(h)` collapsed to 3 chunks while `(i)` inflated to 112,
  because the first roman `(i)` inside `(h)` matched the next expected letter.

**The fix that works:** long CFR sections open with their own table of contents in a
`<TABLE>`. Parse it for the authoritative letter sequence *and headings*, then require
a candidate paragraph to both match the next expected letter **and** open with that
letter's heading text (`_opens_with`). Genuine `(i)` starts "Representatives of
information media"; a nested roman `(i)` does not.

Result: `(h)` 114 chunks, `(i)` 2 chunks, no cross-classification leakage.
Locked by `tests/test_citations.py` — **do not loosen these**.

**2b. The 214.2 citation fix only ever covered sections that have a table of
contents.** `_toc_letters` + `_opens_with` is authoritative and correct — but it only
runs when a section carries a TOC table. 214.2 has 23 TOC entries, so the fix verified
clean there. **204.5 has none**, so it fell through to `_next_in_sequence`, the strict
next-letter path finding #2 records as broken. After `(h)` it saw `(i)` — genuinely the
next letter *and* the first roman sub-item — and accepted it. The result: the ten EB-1A
criteria were filed under **8 CFR 204.5(i), the outstanding-professor category**, and
204.5(h) was truncated mid-sentence at "at least three of the following:". Eleven
sections were affected (204.3, 204.311, 204.313, 204.5, 212.5, 212.15, 214.6, 235.1,
245.24, 1240.11, 1292.16).

Fixed by `_promises_a_list()`: on the no-TOC path only, a candidate `(i)`/`(v)`/`(x)`
is rejected when the preceding text ends by handing off a list. The TOC path is
untouched, so `test_citations.py` still locks 214.2 exactly as before.

**Do not rely on letter-sequence contiguity to detect this.** The chunker assigns
letters in strict sequence, so promoting a roman list to `(i)` *fills* the sequence —
there is no gap to find. Zero gaps across 580 sections while 11 were wrong. The
invariants that do work are in `tests/test_corpus.py`: a paragraph must not promise a
list it does not contain, and `(i)` after a list-promising paragraph is a sub-item.

**2c. `verify_citations` cannot catch a mislabelled corpus.** It validates the
answer's citations *against the retrieved text*, so when the index itself carries the
wrong citation the check passes. This bug sat live for weeks with every guardrail
green. Corpus correctness has to be tested at the chunker, which is what
`tests/test_corpus.py` now does.

**2d. eCFR flush paragraphs are `FP-1`, never bare `FP`.** The chunker matched
`("P", "FP")`; a plain `FP` tag does not occur anywhere in Title 8. That silently
dropped 1,059 elements / 42k chars — concentrated in enumerated list items, so
264.1(a) promised a list of registration forms and delivered nothing. Now matches
`FP*`. Genuine `<TABLE>` cells (TD/TR, ~10k chars) are still dropped; no
immigration-critical section depends on them.

**3. Network quirks.**
- Python's urllib does **not** use the macOS system trust store → `CERTIFICATE_VERIFY_FAILED`
  on .gov TLS while curl works. Fixed with `certifi`.
- **uscis.gov fingerprints the TLS handshake, not headers.** Every User-Agent /
  Accept / Accept-Language combination returns 403 from urllib; curl with the same UA
  gets 200. Fixed by shelling out to curl on 403/406/429. Don't waste time on headers.
- eCFR 404s on today's date. Must resolve the edition first via
  `/api/versioner/v1/titles.json` → `latest_issue_date`. `latest_amended_on` is the
  legally meaningful currency and is stored in the manifest.

**4. No vector database, deliberately.**
~8.5k chunks. Brute-force `vectors @ q` is a single matmul, ~3 ms — *exact*, not
approximate. LanceDB/FAISS would solve a scale problem this corpus will never reach
while adding a dependency and a file format. Two plain files (`chunks.parquet` +
`vectors.npy`) are also trivially shareable.

**5. The gate threshold is calibrated, not guessed.**
Measured across 7 in-domain and 5 off-domain probes:
in-domain min **0.688**, off-domain max **0.544** → `MIN_SCORE = 0.62`.
The original 0.34 was far too permissive — "best pizza topping in Chicago" scored 0.46
and sailed through the gate. **Re-measure if the embedding model changes**; nomic has a
high similarity floor and these numbers are model-specific.

**6. The model does fabricate citations.** Observed live: `[6 CFR § 204.5(d)]` — source
number welded onto an invented provision. Hence `answer.verify_citations()`, which
checks bracket ranges and validates every `N CFR §X` / `N U.S.C. §Y` against retrieved
text. Post-generation validation is not optional here.

**7. A smaller model is better here, not a compromise. Measured on this corpus:**

| | qwen2.5:7b | qwen2.5:14b |
|---|---|---|
| evals | **13/16** | 12/16 |
| wall time | **185 s** | 361 s |
| Ollama reserves | **5.5 GB** | 11.0 GB |
| swap it forces | **none** | **+4.6 GB** |

On an 18 GB M3 Pro the real headroom beside a normal working set is ~6 GB, so the 14b
evicts ~4.6 GB of running applications to disk on every substantial query. That is what
made the tool unusable alongside other work. There is no quantisation of a 14B that
fits: weights alone are 9 GB, and Q2_K (~5.8 GB + KV) degrades below a good 7B Q4.

The failures are complementary, which is the interesting part: 7b is perfect on
substance (STEM 6/6, NIW 4/4) and misses only *dates*; 14b holds dates and collapses on
substance (NIW 1/4). Since the dates are computed in Python anyway, 7b's weakness is
the one the architecture can remove — see finding 8.

**7b. `keep_alive` was never sent, so a model squatted for 5 minutes after every
answer.** `stream_chat` sent no `keep_alive`, so Ollama applied its 5-minute default
and held 5.5–11 GB long after the answer printed. Now `settings.keep_alive` (default
90 s). Verified: resident during generation, released 90 s after the last question.
`OLLAMA_NUM_PARALLEL` needs no override — Ollama already auto-sizes to one slot here
(5.6 GB = 4.7 GB weights + ~0.9 GB KV at 16k).

**8. `verify_dates` catches a wrong date, never an omitted one** — and omission is the
common failure. Three of the eval's four date checks fail by silence, not error. The
deadlines are deterministic Python already, so `_answer_once` now prints
`dates.render()` directly instead of hoping the model repeats it. Note this does *not*
move eval scores: `evals/run.py` calls `stream_chat` directly and never goes through
the CLI, so it scores the model's prose while the user receives prose **plus** Python's
arithmetic.

**9. The vector store is not a bottleneck and never will be.** Measured: dense matmul
over all 8,475 chunks is **0.46 ms**; `Index.load()` is 1.4 s cold; the whole vector
file is 24.8 MB. Meanwhile prefill of a 10.5k-token prompt is ~45 s on 7b — **~89% of
the wait**, against ~3 s of generation. Optimising retrieval speed optimises noise.
The lever is *fewer, better tokens*: fix the citation cap, guarantee the governing
provision, rerank and pass fewer chunks.

**10. No model available on this machine — 14b included — can assess EB-1A eligibility
reliably.** Five models were given a deliberately borderline record; every one made at
least one consequential error, and three claimed the candidate had no scholarly
publications when the record listed 14. command-r7b asserted that lacking association
membership "would likely lead to a denial", which is flatly wrong (any 3 of 10) and
drew **zero** guardrail flags. Decomposing into one call per criterion cut prompt
tokens 10,523 → ~3,400 and made the 3-of-10 threshold arithmetic exact in Python, but
did **not** fix per-criterion accuracy. Caveat: all of this ran against the *broken*
corpus, where retrieval returned no regulation at all — rerun before trusting it.

**10b. Finding 10 was rerun on 2026-09-09 and its headline does not survive.** With the
corpus fixed and `204.5(h)` **force-injected** into every call — one call per criterion,
`qwen2.5:7b`, temperature 0, 3 repeats — the model got **7 of 7 clear-cut criteria
right**, stably (3/3 identical on every criterion):

```
(i) prizes      NOT MET  ✓     (vi) authorship   MET      ✓
(ii) membership NOT MET  ✓     (vii) exhibits    NOT MET  ✓
(iii) published NOT MET  ✓     (x) commercial    NOT MET  ✓
(iv) judging    MET      ✓
```

Both consequential errors finding 10 recorded failed to reproduce. The phantom "no
scholarly publications" is gone — it cited "14 peer-reviewed publications" correctly.
And the membership trap was handled for the right reason: *"IEEE with open membership,
which does not require outstanding achievements."* No invented facts about the candidate
in any run.

**What actually fails is not the reasoning.** Three things, none of them what finding 10
blamed:
1. **Retrieval never delivers the governing regulation** — see 10c. This spike had to
   inject it by hand; the live tool would not have had it.
2. **Final-merits judgment is over-generous.** Criterion (ix) was called MET on a
   $145k salary against a $130k median. An 11.5% premium is not "significantly high
   remuneration", and this is exactly the gap the README already declines to cross.
3. **Format compliance fails the same way citations do.** Criterion (v) echoed the
   template back verbatim (`VERDICT: MET | NOT MET | INSUFFICIENT EVIDENCE`) instead of
   choosing, and (viii) collapsed a three-line format onto one. Note the scoring harness
   mis-read (v) as MET because the regex matched the template — the instrument had the
   same bug as the model. Constrained decoding makes both impossible.

**Also confirms finding 0d's mechanism.** Every criterion was 3/3 stable here. This path
makes no `plan_issues` call, which is the only temperature-0.3 call in the pipeline.
That is direct evidence the eval suite's variance comes from issue planning, not
generation — so pinning `plan_issues` is the right fix, not a guess.

Caveats: one synthetic borderline record, one model, ground truth authored in-house,
and per-criterion verdicts are not an overall eligibility assessment — which remains the
dangerous output and is still not shipped.

**10c. Retrieval returns the wrong visa category for EB-1A, and it is not close.**
The corpus is correct: `204.5(h)` is a single chunk carrying all ten criteria verbatim,
and `204.5(i)` is properly the outstanding-professor category — finding 2b is dead.
But retrieval does not surface it:

```
"EB-1A extraordinary ability criteria"          → tier-2 hits are 8 CFR 214.2(o)  (O-1!)
"evidence of extraordinary ability 3 of 10"     → tier-2 hits are 8 CFR 214.2(o)  (O-1!)
"original contributions of major significance"  → tier-2 hits: 0 of 8
```

The third is the tell: that is the *literal text of criterion (v)*, present verbatim in
the `204.5(h)` chunk, and it cannot be found by its own words.

Mechanism is granularity, not embedding quality — and **the chunker was not
misbehaving**. `204.5(h)` measures **703 tokens against a 700 budget** (`ntokens` is
words x 1.33, not chars/4 — an earlier note in this file said 848 and was wrong). It sat
within budget and was packed exactly as designed. The defect is that one vector was made
to stand for ten independent legal tests plus the definition, the 3-of-10 gate and the
employment clause, so it matches none of them sharply. `214.2(o)` is split into many
focused chunks whose criteria are worded near-identically to EB-1A's.
**The correct provision loses to the wrong one because the wrong one is better chunked** —
and O-1 is a nonimmigrant category with a different standard.

The lesson generalises past this section: a token budget is the wrong instrument for a
provision that enumerates alternatives. Size was never the signal; *how many separable
legal tests share one embedding* is.

This is the "retrieval does not guarantee the governing provision" gap with a mechanism
and a worked example. Two candidate fixes: split long provisions per enumerated
paragraph, or rerank retrieved candidates with a small cross-encoder instructed to
prefer binding immigrant-category regulation. They are complementary.

**10d. Splitting the enumeration helped, and did not fix it. Tier weight is the
binding constraint.** `_split_enumerated` (>= 4 enumerated items and >= 400 tokens ->
stem plus one piece per item, each carrying the stem's tail) turned `204.5(h)` from one
703-token chunk into **11 chunks of 194-352 tokens**. Corpus 8,305 -> 10,779.

Result on the five finding-10c probes: **2 of 5 fixed, 3 unchanged.** General EB-1A
queries now surface the regulation where they previously returned none. But the three
queries that name a *single criterion in its own regulatory wording* still return zero
`204.5(h)`, which was the flagship failure.

Do not misdiagnose this as an embedding problem — the traces rule out every easy
explanation in turn:

```
query: "commanded a high salary compared to others in the field"
  dense: target chunk cosine 0.613, rank  60   (PM chunks 0.70-0.80)
  BM25:  target chunk rank 2 of 795 scored   <- the lexical half WORKS
  fused: target absent from top 8
```

BM25 finds it exactly as designed. The loss happens in fusion, and it is not a fusion
bug either: the two chunks that outrank it are Policy Manual passages that *also*
contain the phrase, legitimately winning on both signals because they discuss the
criterion at length while the regulation merely states it. Arithmetic:

```
target  (dense 60, lex 2):  1/121 + 1/63  = 0.0241  x 0.97 (regulation) = 0.0234
PM      (dense  0, lex 0):  1/61  + 1/61  = 0.0328  x 0.92 (policy)     = 0.0302
```

**Precedence is encoded as a 5% score multiplier, which cannot close a 60-rank gap.**
(Originally written here as "5% cannot express that the binding regulation outranks
commentary quoting it" — too strong, and 10h disproves it. The weight is sufficient when
ranks are close: criterion (ii) now takes its slot at 0.0301 against a Policy Manual
chunk at 0.0299, a 0.7% margin that exists only because of the tier weight. The defect
was the rank, not the weight.) In a legal research tool precedence is not
a tiebreaker; it is an ordering principle, and `TIERS` currently makes it a nudge. This
is the same class of error as finding 11, where the question's own words were left to
compete on score instead of being given reserved slots.

Two candidate fixes, in the codebase's existing idiom:
1. **Reserve slots by tier** — guarantee the highest-tier source matching a query a
   floor of the slate, exactly as `primary_share` does for the question's own words.
   Deterministic, no new model, no new dependency.
2. **Rerank** a wider candidate pool with a 0.6B cross-encoder instructed to prefer
   binding immigrant-category regulation. Note the target sits at dense rank 60-145, so
   a reranker over the usual top-50 would never see it — the candidate pool has to widen
   with it.

(1) is cheaper and should be measured before (2) is built.

A note on the split's own cost: each piece carries 120 tokens of stem so a criterion is
never severed from "at least three of the following", and that stem dilutes the piece's
embedding — the criterion's distinctive wording is ~35 tokens against ~120 of shared
preamble, which is part of why cosine sits at 0.61. Decoupling embedding text from
served text would fix it properly and needs a `Chunk` schema change. Not attempted.

**10e. The split regressed the evals, and the isolation is clean.** Two changes landed
together, so both were measured apart:

```
baseline (neither)                       12-13/16
A only   (temp pin, pre-split corpus)    12/16 (75%)   niw_standard 4/4
A + B    (temp pin, split corpus)        10/16 (62%)   niw_standard 2/4
```

**B is the regression.** And the cause is not what the first diagnosis assumed: the
*Dhanasar* chunk (`USCIS PM Vol 6, Pt F, Ch 5`) sits 1-of-12 in the slate on **both**
corpora — it was never displaced. What changed is the tier-4 NIW policy alerts, **5
slots down to 3**, pushed out by *three fragments of `8 CFR 204.12(c)`* taking the top
of the slate at 0.77/0.77/0.76. The two failing checks, `prong_positioned` and
`prong_balance`, are the prongs that founder-specific guidance carries.

**Splitting inflates a provision's footprint from 1 slot to `per_citation_cap`.** The
cap is per *citation*, and fragments of one provision share their citation, so a
provision that could only ever contribute one chunk can now contribute three. The cap
behaved exactly as documented; the split changed the supply beneath it.

**This rules out the fix proposed in 10d.** "Reserve slots for the highest tier" is
wrong, and finding 1 says why: *Dhanasar* and *Kazarian* live **only** in the Policy
Manual, so NIW needs tier 3-4 while the EB-1A criteria need tier 2. Any fixed tier
preference breaks one to serve the other. What the evidence supports is a **floor per
tier** — every tier with candidates above threshold keeps a minimum share, so newly
fragmented regulation cannot crowd out policy and vice versa.

B is parked unmerged for this reason: its benefit is unrealised (2 of 5 probes) while
its cost is measured (-2 checks, and the NIW answer the README uses as its worked
example). It returns with the slate-allocation fix, measured together.

**A, by contrast, did exactly what it was for.** Every scenario's score now equals its
count of unflagged checks — `stem` 3/6 with 3 flagged, `travel` 2/3 with 1 flagged.
That arithmetic is only reachable if every repeat produced an identical answer, and it
is the combination finding 0d proved impossible this morning (4/6 with 3 flagged).
**The instrument is deterministic again.**

**10f. The regression was a per-*section* crowding bug, and the tier floor 10e
proposed would not have fixed it.** Checked before building: the chunk carrying all
three *Dhanasar* prongs is in the slate on **both** corpora — position 9 pre-split,
10 post-split. Nothing was displaced. Only **4 chunks in the whole corpus** carry all
three prongs, all in `USCIS PM Vol 6, Pt F, Ch 5`, and exactly one reaches the slate
either way. Tier 4 falling 5 slots to 3 removed nothing that mattered, and a floor of 2
would never have fired.

What actually changed is one *section*:

```
PRE-SPLIT   8 CFR 204.12: 2 slots    regulation 3 of 12
SPLIT       8 CFR 204.12: 5 slots    regulation 5 of 12
            (c) x3, (g) x1, (d) x1   <- every subsection inside the cap of 3
```

**8 CFR 204.12 is the national interest waiver for *physicians*.** On a general NIW
question the model received five chunks of the wrong standard against one carrying
*Dhanasar*, and answered from the wrong one. `per_citation_cap` is blind to this by
construction: after a split the fragments share a citation while sibling subsections
carry different ones, so one law arrives in pieces and no counter ever trips.

Fixed with `settings.per_section_cap` (default 4) keyed on `search.section_key`, applied
in both `search` and `search_many`. Kept **above** `per_citation_cap` on purpose — a
lower value would starve `204.5(h)`, which is the whole reason B exists. Locked by
`tests/test_retrieval.py::test_no_single_section_dominates_the_slate`.

Result: `niw_standard` 2/4 -> **4/4**, suite 10/16 -> **12/16**, parity with main.

**10g. B is now neutral, not positive — and its own goal is still unmet.** With the
section cap the branch scores 12/16 against main's 12/16, differing only in
composition (+1 `stem_self_employment`, -1 `travel_on_opt`).

That -1 is not a retrieval loss. `timely_filed`'s supporting evidence is **4 of 12 slate
chunks on both corpora**, and `sept_30` is carried by **zero** slate chunks on either —
it comes from the computed-deadline block, and per finding 8 the evals bypass the CLI
that prints it. The slates differ by a single chunk at position 9 vs 12. A check flipped
whose evidence never moved: a generation knife-edge, and finding 11's warning that a
one-check delta sits below this instrument's resolution — still true now that runs are
deterministic.

Meanwhile B's actual purpose is **still 2 of 5 probes**. The three that fail are the ones
naming a criterion in its own regulatory wording, and the section cap does not touch
their cause: criterion chunks sit at cosine 0.61 / dense rank 60-145 and lose to Policy
Manual commentary that discusses them at length. That is the tier-weight problem in 10d,
untouched.

**So B stays parked.** It costs +30% corpus size (8,305 -> 10,779 chunks) for a benefit
not yet realised, which is the wrong trade for a tool whose next milestone is running
cool enough to ship. It merges when the ranking fix lands and the EB-1A probes actually
pass. The section cap is independent of all that and can go to main on its own — where
it is a no-op guard, since 204.12 takes only 2 slots on the unsplit corpus.

**10h. The criteria were unfindable because the wrong text was being embedded. B is
now net positive and unparks.** The fix is neither of the two things 10d and 10e
proposed. Measured cost of the stem each split piece carries:

```
                    as indexed   item+gate   item alone   competitor
(v) contributions      0.631       0.717       0.744        0.699
(ii) membership        0.642       0.743       0.775        0.804
(ix) salary            0.628       0.705       0.786        0.734
```

~35 tokens of distinctive law were being embedded alongside ~120 of preamble shared by
every sibling piece, costing **0.11-0.16 cosine**. So `Chunk.embed_text` now decouples
what is embedded from what is served: a criterion is *shown* with the stem that governs
it — severed from "at least three of the following" it reads as a requirement — and
*embedded* alone. `_embed_override` returns empty whenever a piece is embedded exactly
as served, so every chunk the enumerated split did not touch keeps its vector unchanged.
Build-time only; `embed_text` is never persisted, so `store.COLUMNS` is untouched.

**Retrieval probes: 5 of 5**, from 2 of 5 after the split alone and 0 of 5 before it.
`214.2(o)` — the O-1 nonimmigrant provision that used to win every EB-1A query — now
returns 0 hits on the two flagship probes.

```
                     stem  niw  opt  travel   total
main (A + cap)        3/6  4/4  3/3   2/3     12/16
branch (+ B + embed)  4/6  4/4  3/3   2/3     13/16
```

**`everify` passes for the first time today.** It is one of the two checks the
2026-08-25 reasoning spec was written to fix and had failed in every prior run — fixed
here by retrieval, not by prompt work.

Note the mechanism, because it was mispredicted twice. Criterion (ii) still *loses on
cosine* (0.715 against 0.804) and wins anyway: dense rank moved **145 -> 3**, and with
BM25 rank 4 the fused score plus the tier weight takes the slot. Predicting from cosine
alone was wrong in a hybrid system.

One behaviour to watch: a general EB-1A query now returns **1** chunk of `204.5(h)`
where it returned 2-3, because sharper per-criterion vectors match a broad query less
often. Correct, but an answer needing the stem plus several criteria at once has less to
work with. Nothing in the current evals exercises that.

**11. Retrieval must reserve a share of the slate for the question's own words.**
`search_many` interleaved round-robin, so with six spotted issues the user's actual
question held a *seventh* of the slate. A founder asking about life after OPT got L-1
and EB-5 passages while the SEVP self-employment bar — the rule that decides the case —
fell to 1 chunk of 12, against 6 on a plain single search. Finding 0c is right that
issue decomposition beats one query; it is wrong if the decomposition *replaces* the
question. `settings.primary_share` (0.5) now floors the question's own hits and issues
interleave for the remainder. Also made `per_citation_cap` match its documentation: a
real 3, not 4-then-uncapped.

Evals were unresolved on this — 14/16 before, 13/16 after — but both slates carry the
same content (5 E-Verify chunks, 4 unemployment chunks) in a different order, so the
delta is ordering and sits **below the resolution of a 16-check instrument**. Noted
because it is the second time a change has been judged by a number too coarse to carry
the judgement.

**12. The system prompt had written "do not invent law" as "do not think".**
Asked what a founder's options were after OPT, the tool returned a correct, cited,
generic enumeration that never reached the rule deciding the case. Retrieval was not
the constraint — on the STEM scenario the model receives 7 SEVP chunks, 5 mentioning
E-Verify, and still failed the `self_employment` check. Rule 1 said answer only from
sources and never fill a gap from memory; nothing anywhere asked the model to connect
those sources to the person asking.

SYSTEM is now two blocks. **Where the law comes from** is unchanged and absolute.
**What to do with it** is new and required: work out which sources govern this person,
lead with what their own facts settle, name the fact a rule turns on rather than
enumerating branches, close with at most one question — after answering, never instead.

Asking a 7b to reason immediately produced fluent *uncited* advice and two invented
facts about the user (a STEM degree, a grace period they are not in) with every
existing check green — `verify_citations` validates brackets that exist and says
nothing about an answer citing nothing at all. So the grounding check shipped in the
same commit rather than a later stage: `verify_grounding` surfaces deontic sentences
(must/may/cannot/is eligible…) carrying no bracket. It does not catch bad inference;
it makes unsupported assertion visible, which is the achievable goal.

**13. Conversation state fails in two opposite directions, and both were shipped
before they were caught.**
`_repl` kept nothing between turns, so "what if it isn't enrolled in that?" retrieved on
its own words and embedded toward nothing. Fixing it introduced three regressions
inside one session:

- **Feeding the previous answer back makes a 7b replay it.** Rendered as "you answered:
  …", the second reply repeated three whole sections of the first verbatim and appended
  one new line. Carry only *what the person said* — their words are the facts of the
  case; the model's own prose is not.
- **Prepending the previous question unconditionally hijacks a self-contained one.**
  "What are the other STEM OPT requirements" with "no i have not filed h1b at all"
  glued on front retrieved 3 H-1B chunks and 0 SEVP ones. Borrow context only when the
  question cannot stand alone — `needs_context()` / `ANAPHORIC_OPENERS`.
- History competes with sources for the same window, which is exactly how finding 0
  happened. Hard-capped at `history_tokens` (700) over `history_turns` (3), condensed on
  whole sentence boundaries — a conclusion cut mid-clause can invert. Order is
  SYSTEM → SOURCES → history → facts/deadlines → question; history goes **above** the
  facts block, never below. The finding-0 guard is extended to assert the deadline block
  survives a full source slate *plus* maximum history.

**14. `verify_dates` was drift-only, and drift-only is the wrong shape of check.**
A live answer wrote "the H-1B start date of October 1, 2027" for a FY2027 cap-gap
running to an October 1 **2026** start. Prose, so the ISO pattern never matched it;
wrong by 366 days, so it was far outside the ±3-day drift window. Every check stayed
green. Now prose dates are parsed, and the rule the tool already applies to citations
applies to dates: **a date belongs in an answer only if it appears in the computed
deadlines or verbatim in a retrieved source.** A date the model worked out for itself is
flagged even when correct — deriving deadlines is the thing deterministic dates exists
to prevent, and right-by-derivation is right by luck.

**14b. Check support first, drift second.** The opposite order reported a correct
"October 1, 2026" as one day off the 2026-09-30 boundary. That is not drift: a window's
close has a neighbour *by design* — cap-gap ends September 30 and the new status begins
October 1 — so it would have fired on every cap-gap answer there is. A warning that
cries wolf systematically is worse than no warning. Also state the start **year** in the
computed note; "an Oct 1 start" is what left the model deriving a year in the first
place, which is what produced October 1 2027.

**15. `dates.compute()` must not assert what status alone cannot derive.** It emitted
the cap-gap window for every F-1 profile, so "[OPEN — 36 days remaining]" sat in front
of the model on every turn and it recommended a cap-gap extension to someone who had
just said they had filed nothing. Cap-gap requires a timely-filed cap-subject H-1B.
Gated on `profile.h1b_filed`.

**16. The Policy Manual's own footnote markers were being read as source numbers.**
The prompt asks for citations as [1], [2] matching *our* numbered sources, then handed
the model PM text carrying **12,778 footnote markers across 75% of its chunks**. It
cannot tell them apart. Every fabricated citation observed that day came from here —
[64], [65], [68], [26], [13], [28] — and the quiet case is worse: a footnote **[3]**
copied into an answer is *in range*, so `verify_citations` passes it while it points at
an unrelated source. This is finding 2c again in a new place: a guardrail that compares
the answer to the corpus cannot see corruption that is already in the corpus.

Stripped at chunk time (`chunk.FOOTNOTE`). Reindexed: 0 markers remaining, PM 2,581 →
2,411 chunks, and no out-of-range citation in a three-turn replay.

**17. On a 7b, prompt length and rule *position* beat rule wording — and citation
compliance is still not fixed.** The finding-12 rewrite doubled SYSTEM to 678 tokens
and pushed the citation rule to the top. Same question, same sources, same temperature:
the old 7-rule prompt cited 4 times, the new 11-rule one cited **zero**. Finding 0
already said the tail is best attended and it applies to instructions, not just data.
Reasoning guidance first, the two non-negotiable rules last, 337 tokens.

**This is where the last session ended.** Compliance improved but remains erratic —
**7, 0, 1 inline citations across three turns** of the same conversation. Few-shot
examples or a post-check that flags uncited claims are the untried options.


---

## Conventions

`uv` + PEP 621, `src/` layout, `pydantic-settings` for config (`VISA_*` env vars),
Pydantic models for payload schemas (`models.py`: `Chunk`, `Manifest`), `ruff` and
`mypy --strict` both clean, `py.typed` shipped. Commits are short and conventional
with no AI attribution.

Gate before pushing: `ruff check src tests && mypy && pytest`

## Architecture

```
~/Projects/visa-rag/    code (git)
~/.visa/corpus/<slug>/  raw/ + chunks.parquet + vectors.npy + manifest.json
~/.visa/me/             profile.toml, docs/, memory/, shard/ — private, gitignored
```

Shards are independent: adding one reindexes nothing else, search concatenates those in
scope, personal docs are the same mechanism with `private = true`. That last part is
what makes the v2 share-guard a one-line invariant instead of a special case.

Retrieval is hybrid — BM25 fused with dense vectors via reciprocal-rank fusion. Pure
embeddings retrieve badly on exact citations (`214.2(f)(10)`) and terms of art, which is
most of what legal lookup asks for. Tier weighting nudges statute above policy. At most
`per_citation_cap` (3) chunks per citation so one sprawling section can't crowd out the
evidence — honoured in `search_many` as of finding 11, which also reserves
`primary_share` (0.5) of the slate for the question's own words before spotted issues
interleave for the rest.

The REPL is stateful: `answer.Turn` history is condensed and carried into both the
prompt and the retrieval query, budgeted against `num_ctx` (findings 13, 0).
Post-generation checks are `verify_citations`, `verify_dates`, `verify_grounding` and
`verify_dialogue`; all four report into the answer-check panel.

---

## Known gaps / next steps

**Quality**
- **README publishes an unreproducible number.** It states a flat 81% from a run that
  today reproduces as 75%. For a project whose whole posture is honesty about limits, a
  headline accuracy claim that moves a full check between samples needs to be a range
  with its variance stated, or the lower bound. Not yet changed — it is a user-facing
  claim and wants a deliberate decision, not a silent edit.
- **Citation compliance is the open problem — start here.** Erratic across turns of one
  conversation: 7, then 0, then 1 inline `[n]`. Finding 16 removed the corpus
  contamination and finding 17 shortened SYSTEM and moved the rule to the tail, which
  improved it without fixing it. Untried: few-shot examples, or a post-check that flags
  uncited claims (`verify_grounding` flags only deontic sentences today).
- `uscis-policy-updates` yields only 5 chunks from 2 PDFs. Plausible (policy alerts are
  short) but **unverified** — worth confirming pypdf isn't silently dropping text.
  `chunk_pdf` skips pages under 25 words *silently*, so a scanned PDF ingests as zero
  chunks with no error. Any user-facing ingest must refuse that loudly.
- Retrieval noise: NIW query pulled `USCIS PM Vol 9, Pt O` (waivers of inadmissibility)
  alongside the right chapters. Tier weighting alone doesn't fix topical drift.
- Retrieval does not guarantee the governing provision. An EB-1A question returned 12/12
  Policy Manual chunks and zero regulation. Embeddings won't reliably surface
  204.5(h)(3) from a narrative question; an intent → mandatory-citation table would.
- **Evals don't cover the primary use case, and are too coarse for the changes now
  being made.** Four scenarios, 16 checks, all extraction and dates. Nothing tests
  reasoning over the user's own documents against a legal standard (finding 10), and
  nothing tests dialogue or citation compliance at all — the two things the last session
  worked on. A one-check delta is inside the noise floor (finding 11) and now inside
  run-to-run variance as well (finding 0d) — runs are **not** deterministic any more,
  so `--repeat` is mandatory, not a convenience.
- ~41 s per answer with qwen2.5:7b, machine stays usable throughout (5.06 GB headroom
  at peak, zero new swap). See finding 7.

**Not built yet**
- **Stage 4 of the reasoning/dialogue spec — the persisted situation model (`/facts`).**
  Stages 1-3 (prompt split + answer-then-ask, conversation state, grounding check)
  shipped 2026-08-25. Stage 4 extracts durable facts the user *states* — "I control the
  entity", "my degree is CS" — into `~/.visa/me/`, shown and correctable, so the second
  conversation starts knowing what the first established. Facts are the user's
  assertions, never the model's inferences. Its three open questions are still open: how
  many turns of history and summarised how; whether extraction runs every turn (latency)
  or on an explicit `/remember`; whether the situation model is shown at session start or
  only on `/facts`. Spec: `docs/superpowers/specs/2026-08-25-reasoning-and-dialogue-design.md`.

**Not built yet (v2)**
- `visa share` — bundle raw + index + manifest, refuse to include `~/.visa/me/`.
  Ship **both** source and index: embedding-model lock-in (manifest already stamps
  `embed_model`, `Shard.check_embed_model()` refuses mismatches), verifiability via the
  per-file SHA-256s already recorded in the manifest, and re-chunking invalidates the
  index but not the source.
- Install story for non-technical students: Ollama + a ~5 GB model + Python is not
  one-click and never will be. Be honest about this rather than over-promising.
- `visa ingest` — offline, file-drop ingestion of USCIS material the user downloads
  themselves: an inbox folder, a command (later a UI), a validation gate, landing in a
  **marked** user-supplied shard at a new tier 5 (weight below `guidance` 0.88) so
  curated sources always outrank it. Shareable but visibly unvetted. `chunk_pdf`
  already emits honest `"{title} (p. N)"` citations, so this does not require inventing
  legal provisions. Design agreed, spec not yet written.
- AAO/adopted decisions as a tier-4 source (how adjudicators reason on close calls).

**Deliberately excluded**
- **Visa Bulletin.** Monthly and volatile — the July 2026 edition reports India EB-2
  unavailable for the remainder of FY2026. A cached snapshot answering priority-date
  questions confidently is the one genuinely dangerous failure mode of an offline tool.
  `answer.needs_live_bulletin()` refuses and points at travel.state.gov. Keep it that way.

---

## Context

Built for international students and founders who cannot easily afford counsel and would
rather not post their immigration details publicly. Intended to be shared with
other international students (v2). Founder-track categories — EB-1A, EB-2 NIW, O-1A —
are the priority use case, which is exactly why the Policy Manual tier matters most.

Design doc: `docs/superpowers/specs/2026-07-29-visa-rag-design.md`
