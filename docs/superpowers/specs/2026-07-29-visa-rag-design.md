# visa-rag — a local, offline, citation-gated immigration research tool

**Date:** 2026-07-29
**Status:** v1 in progress

## Problem

International students make status-critical decisions (OPT filing windows, travel,
employer changes, EB-1A/NIW/O-1 strategy) without affordable access to counsel.
Public forums are a poor substitute: answers are unsourced, often stale, and posting
personal details is uncomfortable given the current climate.

A general LLM is worse than nothing here. Asked "do I qualify for a national interest
waiver," a model answers fluently from memory and is subtly wrong a meaningful
fraction of the time. Wrong deadlines and hallucinated standards have real
consequences.

## Approach

Do not ask the model to *recall* immigration law. Make it *read* the law, locally,
and quote it — or admit it has nothing.

Three properties define the system:

1. **Citation-gated.** Every substantive claim carries a source, a tier, a verbatim
   quote, and a fetch date. Below the retrieval threshold, the tool says it has no
   supporting text instead of improvising.
2. **Offline.** After `visa init`, zero network. No telemetry. Nothing leaves the machine.
3. **Extensible.** Corpora are independent shards declared in TOML. Adding one
   reindexes nothing else.

### Why the regulation alone is insufficient

Verified during design, and the finding that shaped the corpus:

- **8 CFR 204.5(k)(4)(ii)** — the entire regulatory text on the national interest
  waiver — says only that USCIS "may exempt the requirement of a job offer ... if
  exemption would be in the national interest." It never defines the term.
- The operative test (*substantial merit and national importance* / *well positioned
  to advance* / *on balance beneficial to waive*) comes from **Matter of Dhanasar**,
  26 I&N Dec. 884 (AAO 2016). Grepping the full Title 8 XML: **absent**.
- EB-1A is the same shape. The reg lists ten criteria; the two-step analysis USCIS
  actually applies (criteria count, then a separate *final merits determination*)
  comes from **Kazarian v. USCIS**, 596 F.3d 1115 (9th Cir. 2010). Also absent.

A CFR-only index would retrieve one empty sentence and fall back on model memory —
the exact failure this project exists to prevent, hitting hardest on the categories
founders care about. The **USCIS Policy Manual** tier is therefore mandatory, not
optional: it encodes both tests (`Dhanasar` ×3, `Kazarian` ×4, "final merits" ×5,
"entrepreneur" ×38).

## Corpus — four tiers, ranked by legal precedence

| Tier | Source | Fetch | Size |
|---|---|---|---|
| 1 statute | INA / 8 U.S.C. | uscode.house.gov XML zip | 1.2 MB |
| 2 regulation | 8 CFR (full Title 8) | eCFR versioner API, XML, dated | 5.4 MB |
| 3 policy | USCIS Policy Manual (all 12 vols) | single HTML export, `/book/export/html/68600` | 11.8 MB |
| 4 guidance | NIW/entrepreneur policy updates | USCIS PDFs | 0.2 MB |

Precedence is carried as chunk metadata and surfaced in answers: a Policy Manual
statement is USCIS's *interpretation*, not law, and the tool says so.

### Measured

~2.0M words → **3,787 chunks** → index **26 MB** (fp32). Brute-force search over the
whole corpus is a 3,787×768 matmul, **~3 ms**. One-time embedding build: 2–5 min on M3 Pro.

**Consequence: no vector database.** LanceDB/FAISS solve approximate search at a
scale this corpus will never reach. `numpy` + `parquet` gives *exact* search, one
fewer dependency, and a portable two-file shard with no format lock-in.

The real retrieval quality lever is **hybrid search** — BM25 fused with vectors.
Pure embeddings retrieve poorly on exact citations (`214.2(f)(10)`) and terms of art.

## Storage layout

```
~/Projects/visa-rag/          code — git, public, no personal data
~/.visa/corpus/<slug>/        raw/ + chunks.parquet + vectors.npy + manifest.json
~/.visa/me/                   profile.toml, docs/, memory/ — private shard, never shared
```

Each shard is independent: add one, reindex nothing else; search concatenates shards
in scope; share any subset. Personal documents use the identical mechanism with
`private = true`, which makes the v2 share-guard a one-line invariant rather than a
special case.

A source declares itself in TOML: url, fetcher (`ecfr-xml` | `uscode-zip` | `html` |
`pdf` | `text`), chunker, tier, jurisdiction, refresh cadence.

## Personalization

The model's weights never change — what improves is retrieved context. This is
strictly better: auditable, inspectable, deletable.

- `profile.toml` — **structured** facts: status, program end date, degree level,
  employer, country of chargeability, I-94 expiry, prior filings.
- Structured rather than freeform because immigration is largely **date arithmetic**,
  which LLMs do unreliably. OPT windows (90 days before → 60 days after program end),
  grace periods, and cap-gap are computed in Python and *given* to the model, which
  explains rather than calculates.
- `memory/` — freeform notes, embedded into the private shard.

## The offline exception

The **Visa Bulletin** is monthly and volatile — the July 2026 edition reports India
EB-2 unavailable for the remainder of FY 2026. A stale snapshot answering priority-date
questions confidently would be dangerous. It is deliberately excluded from corpus
knowledge; priority-date questions hit a hard freshness gate that refuses when the
bulletin is older than 35 days. Statute, regulation, and Policy Manual move slowly
enough to be safely offline.

## Sharing (v2)

Bundle = raw source + index + manifest (~43 MB), because:

- **Embedding lock-in** — an index is valid only for the exact embedding model and
  version; the manifest stamps it and loading refuses on mismatch rather than
  silently returning garbage neighbors.
- **Verifiability** — per-file SHA-256 against the `.gov` originals. People trusting
  this instead of a lawyer must be able to verify the text, or rebuild it in 5 minutes.
- **Re-chunking** — improving the chunker invalidates the index, not the source.

## Non-goals

- Not legal advice. It quotes primary sources and refuses without them.
- Cannot predict approval. For EB-1A/NIW the gap between "meets three criteria on
  paper" and "survives the final merits determination" is where petitions actually
  die, and that is an evidence-quality judgment.
- Not a replacement for a DSO, who is free and controls the SEVIS record.

## v1 scope

Corpus ingest (4 tiers) · shard storage · hybrid search · citation gate · profile +
date math · private doc ingest · CLI. Extensibility ships in v1 because retrofitting
it would mean rewriting storage. Packaging and `visa share` are v2.
