# visa

Local, offline research over primary U.S. immigration sources. Nothing leaves your machine.

Built because immigration questions are expensive to ask a lawyer, uncomfortable to ask
in public, and dangerous to ask a chatbot — a general model will answer the F-1 grace
period or the NIW standard fluently and be subtly wrong often enough to matter.

This does not ask the model to *recall* immigration law. It makes the model *read* the
law and quote it, or admit it has nothing.

```
ask> what is the standard for a national interest waiver?

  8 CFR 204.5(k)(4)(ii) says only that USCIS "may exempt the requirement of a job
  offer ... if exemption would be in the national interest" — the regulation never
  defines the term [7].

  The operative test comes from Matter of Dhanasar (AAO 2016) [1]:
    1. substantial merit and national importance
    2. well positioned to advance the proposed endeavor
    3. on balance, beneficial to waive the labor certification

  ── sources ──────────────────────────────────────────
   [1] USCIS PM Vol 6, Pt F, Ch 5      policy      0.77
   [7] 8 CFR § 204.5(k)                regulation  0.72
  ── not legal advice · confirm with your DSO ─────────
```

## Install

Needs [Ollama](https://ollama.com) and Python 3.11+.

```bash
ollama pull nomic-embed-text     # 274 MB — embeddings
ollama pull qwen2.5:7b           # 4.7 GB — reasoning

pip install -e .
visa init                        # downloads the corpus, builds the index (~6 min)
```

`visa init` is the only step that touches the network. After it, you can turn off wifi.

## Use

```bash
visa                             # REPL
visa ask "when can I file for OPT?"

visa profile program_end_date 2027-05-14   # your facts, stored locally
visa add ~/Downloads/rfe.pdf               # your documents, indexed privately
visa remember "changed employer in March"  # freeform context

visa sources                     # what's indexed, precedence, freshness
visa refresh                     # re-pull anything stale
```

In the REPL, `/sources 3` prints passage 3 verbatim — use it. It's how you check the
model didn't drift from what the text actually says.

## What's in the corpus

| Tier | Source | Why |
|---|---|---|
| 1 statute | INA / 8 U.S.C. | Highest precedence |
| 2 regulation | 8 CFR (full Title 8) | 214.2(f) F-1/OPT, 214.2(o) O-1, 204.5 EB-1/2/3/5 |
| 3 policy | USCIS Policy Manual, all 12 volumes | **Where the tests actually live** |
| 4 guidance | NIW / entrepreneur policy alerts | Founder-specific framing |

Tier 3 is not optional. The regulation gives EB-1A's ten criteria but not the
*Kazarian* two-step analysis; it authorizes the national interest waiver without
defining it. Both tests exist only in the Policy Manual. A CFR-only index would
retrieve one empty sentence and let the model improvise the rest.

## How it avoids lying to you

- **Citation gate.** Retrieval below a calibrated threshold (0.62 — measured, not
  guessed) refuses outright instead of answering.
- **Citation validator.** Fabricated citations are caught after generation. The model
  has been observed emitting `[6 CFR § 204.5(d)]`; that now raises a visible error.
- **Deterministic dates.** OPT windows, grace periods, and cap-gap are computed in
  Python and given to the model. It explains them; it never calculates them.
- **Precedence.** Answers say when a rule comes from USCIS policy rather than law.
- **Freshness.** Every shard is date-stamped. Priority-date questions are refused
  outright — the Visa Bulletin moves monthly and is deliberately not cached.

## Reasoning about a situation

Describe your circumstances and it decomposes them into legal issues before retrieving —
one search per issue rather than one for the whole story. This matters because the
provision that decides your case is usually the one you didn't know to ask about.

```
ask> I was unemployed for 60 days of my OPT and now work 20 hrs/week at my own startup

  issues identified:
   · unemployment accrual during post-completion OPT
   · employer eligibility requirements for STEM extension
   · bona fide employer-employee relationship where student controls the entity
```

Fill in `visa profile` first — filing windows and grace periods are computed in Python
from your dates and handed to the model as fixed facts, and a date in the answer that
drifts from a computed boundary is flagged rather than shown.

**Verify before relying on it.** Scored on `evals/`, the current setup answers 81% of
checks correctly — good for a research assistant, not good enough to act on unverified.
Those 16 checks cover lookup and dates only; nothing scores reasoning about your own
documents against a legal standard, so treat that use especially sceptically.
Use `/sources N` to read the passage behind any claim.

```bash
python evals/run.py --repeat 3              # score the current setup
python evals/run.py --model qwen2.5:14b     # compare a model
python evals/run.py --strategy single       # compare retrieval strategy
```

## Adding your own corpus

Drop a TOML file in `sources/`:

```toml
slug = "ca-tenant-law"
title = "California tenant protections"
tier  = 2
fetcher = "html"      # ecfr-xml | uscode-zip | html | pdf | text
chunker = "text"
url = "https://..."
```

Then `visa add --source ca-tenant-law.toml`. Shards are independent — adding one
reindexes nothing else.

## Privacy

```
~/.visa/corpus/   public law      — shareable
~/.visa/me/       you             — never shared, gitignored
```

No telemetry, no network after `init`. Your documents and profile are a separate shard
that the (planned) share command refuses to include.

## Limits

- **Not legal advice.** It quotes primary sources; it does not advise.
- **Cannot predict approval.** For EB-1A/NIW the gap between "meets three criteria on
  paper" and "survives the final merits determination" is where petitions die, and
  that's an evidence-quality judgment.
- **Not a substitute for your DSO**, who is free and controls your SEVIS record.
- **The corpus goes stale.** `visa sources` shows you when. Run `visa refresh`.
