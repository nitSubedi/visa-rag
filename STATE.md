# Project state — visa-rag

**Last updated:** 2026-08-18
**Status:** v1 working end-to-end. v2 (sharing/packaging) not started.

---

## Where things stand

`visa init` → `visa ask` works. **8,475 chunks** indexed across four tiers, all five
sources ingesting cleanly. 44 tests passing.

```
8-cfr                 4,247 chunks   tier 2 regulation   (edition 2026-07-21)
uscis-policy-manual   2,581 chunks   tier 3 policy
ina-8usc              1,621 chunks   tier 1 statute
sevp-stem-opt            21 chunks   tier 4 guidance
uscis-policy-updates      5 chunks   tier 4 guidance
```

Verified live: asking for the NIW standard returns the correct *Dhanasar* three prongs,
cited to USCIS PM Vol 6, Pt F, Ch 5. Default model is now `qwen2.5:7b` — see finding 7.

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
3 chunks per citation so one sprawling section can't crowd out the evidence — **but note
`search_many` does not honour that**: round 1 caps at 4 and round 2 tops up with
`citation_cap=k`, i.e. uncapped. An EB-1A query filled 12 slots with 7 distinct
chapters.

---

## Known gaps / next steps

**Quality**
- Model under-cites — often one inline `[n]` for a multi-claim answer. Prompt is firm
  about this; may need few-shot examples or a post-check that flags uncited claims.
- `uscis-policy-updates` yields only 5 chunks from 2 PDFs. Plausible (policy alerts are
  short) but **unverified** — worth confirming pypdf isn't silently dropping text.
  `chunk_pdf` skips pages under 25 words *silently*, so a scanned PDF ingests as zero
  chunks with no error. Any user-facing ingest must refuse that loudly.
- Retrieval noise: NIW query pulled `USCIS PM Vol 9, Pt O` (waivers of inadmissibility)
  alongside the right chapters. Tier weighting alone doesn't fix topical drift.
- Retrieval does not guarantee the governing provision. An EB-1A question returned 12/12
  Policy Manual chunks and zero regulation. Embeddings won't reliably surface
  204.5(h)(3) from a narrative question; an intent → mandatory-citation table would.
- **Evals don't cover the primary use case.** Four scenarios, 16 checks, all extraction
  and dates. Nothing tests reasoning over the user's own documents against a legal
  standard, which is what finding 10 is about. `--repeat` averages; runs are
  deterministic at temperature 0 (verified 3/3 identical).
- ~41 s per answer with qwen2.5:7b, machine stays usable throughout (5.06 GB headroom
  at peak, zero new swap). See finding 7.

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
