# Project state — visa-rag

**Last updated:** 2026-07-29
**Status:** v1 working end-to-end. v2 (sharing/packaging) not started.

---

## Where things stand

`visa init` → `visa ask` works. **8,451 chunks** indexed across four tiers, all four
sources ingesting cleanly. 36 tests passing.

```
8-cfr                 4,244 chunks   tier 2 regulation   (edition 2026-07-21)
uscis-policy-manual   2,581 chunks   tier 3 policy
ina-8usc              1,621 chunks   tier 1 statute
uscis-policy-updates      5 chunks   tier 4 guidance
```

Verified live: asking for the NIW standard returns the correct *Dhanasar* three prongs,
cited to USCIS PM Vol 6, Pt F, Ch 5.

---

## Hard-won findings — do not re-derive these

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

---

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
3 chunks per citation so one sprawling section can't crowd out the evidence.

---

## Known gaps / next steps

**Quality**
- Model under-cites — often one inline `[n]` for a multi-claim answer. Prompt is firm
  about this; may need few-shot examples or a post-check that flags uncited claims.
- `uscis-policy-updates` yields only 5 chunks from 2 PDFs. Plausible (policy alerts are
  short) but **unverified** — worth confirming pypdf isn't silently dropping text.
- Retrieval noise: NIW query pulled `USCIS PM Vol 9, Pt O` (waivers of inadmissibility)
  alongside the right chapters. Tier weighting alone doesn't fix topical drift.
- ~90 s per answer with qwen2.5:14b on 18 GB. Tolerable, not pleasant. `qwen2.5:7b`
  is the fallback if RAM pressure shows up.

**Not built yet (v2)**
- `visa share` — bundle raw + index + manifest, refuse to include `~/.visa/me/`.
  Ship **both** source and index: embedding-model lock-in (manifest already stamps
  `embed_model`, `Shard.check_embed_model()` refuses mismatches), verifiability via the
  per-file SHA-256s already recorded in the manifest, and re-chunking invalidates the
  index but not the source.
- Install story for non-technical students: Ollama + 9 GB model + Python is not
  one-click and never will be. Be honest about this rather than over-promising.
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
