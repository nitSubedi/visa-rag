# Architecture

How `visa` turns primary immigration law into cited answers, and why each stage looks
the way it does. For the hard-won bugs and the measurements behind these choices, see
`STATE.md` — it is the record of what not to re-derive.

The governing idea: **never ask the model to recall immigration law.** Make it read the
law and quote it, or admit it has nothing. Everything below serves that.

```
sources/*.toml → fetch → chunk → embed → store        (once, `visa init`)
                                            ↓
question → plan issues → retrieve → gate → prompt → generate → verify
```

## 1. Ingest — `sources.py`, `fetch.py`

Sources are declarative. A TOML file in `sources/` names a fetcher and a chunker, and
`load_defs()` turns it into a `Source`. Adding a corpus does not require code.

`fetch.fetch()` downloads and records a SHA-256 per file in the manifest, so a shard can
be verified against the .gov original. Three quirks are load-bearing:

- `ecfr_edition()` resolves the published edition first — eCFR 404s on today's date, and
  `latest_amended_on` is the legally meaningful currency.
- `_curl()` is a fallback on 403/406/429. uscis.gov fingerprints the TLS *handshake*, not
  headers, so urllib is refused where curl succeeds. Do not spend time on User-Agents.
- `_ssl_context()` uses `certifi`; Python's urllib ignores the macOS system trust store.

## 2. Chunk — `chunk.py`

Cutting law into retrievable passages. Most of this project's engineering lives here,
because a passage carrying the wrong citation is worse than no answer.

`chunk_source()` dispatches on `src.chunker`: `chunk_ecfr`, `chunk_uscis_pm`,
`chunk_uscode`, `chunk_pdf`, `chunk_text`.

**`chunk_ecfr` is the difficult one.** Regulation paragraphs run `(a)`, `(b)`, `(c)`…
but sub-items use roman numerals, so `(i)`, `(v)` and `(x)` are ambiguous — the letter
after `(h)`, or sub-item one *inside* `(h)`? Four functions resolve it:

| | |
|---|---|
| `_toc_letters()` | long sections open with their own table of contents; parse it for the authoritative letter sequence and headings |
| `_opens_with()` | require a candidate paragraph to also begin with that letter's heading text |
| `_next_in_sequence()` | fallback for sections with no table of contents |
| `_promises_a_list()` | on the no-TOC path, reject `(i)` when the preceding text just handed off a list |

Then the general machinery:

- `ntokens()` — token estimate, `words × 1.33`
- `_pack()` — groups paragraphs to `chunk_tokens` (700), never splitting mid-paragraph
- `_split_enumerated()` — a provision with **≥4 enumerated items and ≥400 tokens** becomes
  its stem plus one piece per item
- `_embed_override()`, `_mk()` — build the `Chunk` model

### Served text and embedded text differ

`_split_enumerated()` returns `(served, embedded)` pairs. Each criterion is **served**
with the stem that governs it — severed from "at least three of the following" a
criterion reads as a requirement rather than one of ten alternatives — but **embedded
alone**, because ~35 tokens of distinctive law under ~120 tokens of shared preamble
costs 0.11–0.16 cosine and buries the provision.

`Chunk.embed_text` carries this. It is build-time only: vectors are computed from it and
it is never persisted, so `store.COLUMNS` is unaffected. `_embed_override()` returns
empty whenever a piece is embedded exactly as served, so anything the enumerated split
did not touch keeps its vector unchanged.

In the literature this is **parent-document retrieval**: search small and precise, show
large and interpretable.

## 3. Embed — `embed.py`

`embed()` calls Ollama `/api/embed` with `nomic-embed-text` in batches of 64, producing
768 floats per chunk.

- Documents get `DOC_PREFIX`, queries get `QUERY_PREFIX`; nomic is trained to
  distinguish them, and `embed_query()` exists so a caller cannot forget.
- Vectors are **unit-normalised**, so cosine similarity is a plain dot product. That is
  why the whole of search is one matrix multiply.

## 4. Store — `store.py`

**There is no vector database, deliberately.** Three files per source:

```
~/.visa/corpus/<slug>/
  chunks.parquet    text + citations, zstd
  vectors.npy       the 768-dim vectors
  manifest.json     provenance, dates, per-file SHA-256, embed_model
```

At this scale `vectors @ query` is **0.46 ms and exact**, not approximate. A vector
database would solve a scale problem this corpus will never reach while adding a
dependency and a file format. Two plain files are also trivially shareable and verifiable,
which is the whole of the planned `visa share`.

Shards are independent: adding one reindexes nothing else, search concatenates those in
scope, and personal documents are the same mechanism with `private = true`. That last
part is what makes the share-guard a one-line invariant rather than a special case.

`Shard.check_embed_model()` refuses a shard built with a different embedding model.
Vectors from different models are not comparable and the failure would otherwise be
silent nonsense.

`~/.visa/me/` — profile, documents, memory — is a separate shard, gitignored, never shared.

## 5. Retrieve — `search.py`

`Index.load()` concatenates every shard and `_build_bm25()` builds a keyword index in
memory at startup.

`search()` is **hybrid retrieval**, because neither half suffices:

1. **Dense** — `self.vectors @ qv`, catches paraphrase.
2. **Sparse** — `_bm25()`, classic BM25 with IDF and length normalisation, catches exact
   terms of art and citations like `214.2(f)(10)` that embeddings retrieve badly.

The two rankings are combined by **reciprocal rank fusion** — `1/(rrf_k + rank)` from
each list, summed — which merges orderings without requiring the scores to share a scale.

Then, in order:

| | |
|---|---|
| `tier_weight` | statute 1.00, regulation 0.97, policy 0.92, guidance 0.88 |
| `per_citation_cap` (3) | one subsection cannot flood the slate |
| `per_section_cap` (4) | one *section* cannot flood it by arriving in pieces |
| `top_k` (12) | what reaches the prompt |

`section_key()` derives the section from a citation, because after the enumerated split
fragments share a citation while sibling subsections carry different ones — so a section
can consume the slate without any per-citation counter tripping.

`search_many()` handles decomposed questions: `primary_share` (0.5) reserves half the
slate for the user's own words, and spotted issues interleave for the remainder. Each
issue retrieves a *full* slate; retrieving `k/n` per issue starves the merge after
cross-issue deduplication.

**`passes_gate()` is the citation gate.** If the best cosine is below `min_score` (0.62)
the tool refuses rather than answering from model memory. That threshold is measured, not
guessed: in-domain queries bottomed at 0.688 and off-domain topped at 0.544. Re-measure
it if `embed_model` changes — the floor is model-specific.

## 6. Generate — `answer.py`

`answer()` orchestrates, and refuses early where refusing is right:

- `needs_live_bulletin()` — priority-date questions are declined outright and pointed at
  travel.state.gov. The Visa Bulletin moves monthly, and a cached snapshot answering it
  confidently is the one genuinely dangerous failure mode of an offline tool.
- `looks_situational()` → `plan_issues()` — decomposes a described situation into 3–6
  legal issues so retrieval reaches the provision the person did not know to ask about.
  Runs at `plan_temperature`, **0.0**: it is a model call too, and leaving it sampling
  made the whole retrieval slate irreproducible.
- `needs_context()` / `retrieval_query()` — a follow-up containing an anaphor borrows the
  prior turn's subject; a self-contained question does not.
- `build_prompt()` — order is deliberate and budgeted against `num_ctx`:

```
SYSTEM → SOURCES → conversation history → profile + COMPUTED DEADLINES → QUESTION
```

Generation keeps the **tail**, so the facts sit closest to the question. History is
hard-capped at `history_tokens` and condensed on whole sentences — a conclusion cut
mid-clause can invert. Anything added here must be budgeted or it will silently push the
deadline block out of context.

`SYSTEM` is two blocks. Where the law comes from is absolute. What to do with it is
required: apply the retrieved rules to this person's facts, lead with what their own
facts settle, close with at most one question — after answering, never instead.

## 7. Deterministic dates — `dates.py`

**Take the model out of the loop wherever determinism exists.** `post_completion_opt()`,
`stem_extension()`, `opt_grace()`, `unemployment_budget()` and `cap_gap()` are pure
Python. `compute()` assembles the windows that apply to a profile and `render()` formats
them into the prompt. The model explains arithmetic; it never performs it.

`cap_gap()` is gated on `profile.h1b_filed` — it requires a timely-filed cap-subject
petition and is not derivable from status alone.

## 8. Verify — `answer.py`

Four post-generation checks, all surfaced in the answer-check panel:

| | |
|---|---|
| `verify_citations()` | fabricated provisions, and bracket numbers outside the slate |
| `verify_dates()` | a date belongs in an answer only if it appears in the computed deadlines or verbatim in a retrieved source. A date the model derived is flagged **even when correct** — right-by-derivation is right by luck. Support is checked before drift, because a window's close has a legitimate neighbour by design |
| `verify_grounding()` | deontic sentences (must, may, cannot, is eligible…) carrying no citation. Deliberately over-inclusive: a false flag costs a glance, a missed one costs a wrong belief about someone's status |
| `verify_dialogue()` | answer-then-ask is enforced, not merely requested |

A guardrail that compares the answer to the corpus cannot see corruption already *in*
the corpus. Corpus correctness is therefore tested at the chunker —
`tests/test_corpus.py` asserts structural invariants over every section, not just the
ones once known to be broken.

## Why not LangChain or LangGraph

Everything that makes this tool correct lives in the layer those frameworks abstract
away. A generic recursive character splitter has no concept of `(h)` versus a nested
roman `(i)`, and would reproduce both citation bugs recorded in `STATE.md`. The absence
of a vector database is a feature, not a gap. And the single largest bug in this
project's history was a prompt-budget failure found by reading `build_prompt` directly —
behind a framework's prompt assembly it would have stayed invisible far longer.

Two honest credits. `ParentDocumentRetriever` / `MultiVectorRetriever` are the same idea
as `embed_text`, so that design is a recognised pattern. And if the per-criterion EB-1A
assessment grows into many typed judgments with verifiers and aggregation, that is a real
graph and LangGraph would deserve a look. Neither is a reason to adopt a framework today.

## Testing

`ruff check src tests && mypy && pytest` before pushing.

`.python-version` pins 3.11, the floor in `requires-python`. Type-checking against
the floor is the point — on 3.12 the installed numpy ships stubs using 3.12-only
syntax, `mypy` cannot parse them under `python_version = "3.11"`, and the whole
gate fails on a file nobody in this repo wrote.

Tests assert **properties**, not transcripts, because the space of real situations is not
enumerable: a paragraph must not promise a list it does not contain; the deadline block
must survive a full source slate plus maximum history; no single section may dominate the
slate. `evals/run.py` scores end-to-end behaviour on scenarios drawn from observed
failures, at temperature 0 — an eval that cannot be reproduced measures nothing.
