# Reasoning and dialogue — design

**Date:** 2026-08-25
**Status:** stages 1-3 implemented 2026-08-25 (C+D `be3fca8`, A `376fece`, E `be3fca8`,
plus fixes `4cdea2b` `fbbf1bc` `456e520` `1f0694f` `5337ec8`). Stage 4 (B, the persisted
situation model) not started; its open questions below are still open.

## The problem

Asked "I am a founding engineer of my startup, we are in pilot phase, what are the
options for me after my one year OPT ends?", the tool returned a correct, cited,
generic enumeration: STEM OPT extension, cap-gap, change of status, other options.
Everything it said was true. None of it was *about the person asking*.

It never reached the fact that decides the case — a founder who controls the entity
cannot self-certify a Form I-983, and can only use STEM OPT through a bona fide
employer-employee relationship at an E-Verify enrolled employer. That guidance was in
the prompt. On the eval's STEM scenario the model receives **7 SEVP chunks, 5 of them
mentioning E-Verify**, and still fails the `self_employment` check. Retrieval is not
the binding constraint.

Two structural causes:

**1. The REPL is stateless.** `_repl` loops on `_answer_once(idx, q, k)` and retains
nothing between turns; `last` exists only to serve `/sources N`. Every question is
answered as if asked by a stranger. A tool that cannot hold context cannot guide
anyone through anything.

**2. The system prompt forbids application.** Rule 1 says answer ONLY from sources and
never fill a gap from memory; rule 7 says be brief, no preamble. Both are load-bearing
for the anti-hallucination story and neither should be removed. But nothing asks the
model to apply the retrieved rules to the user's facts, and rule 6 merely *permits*
mapping evidence to criteria. The prompt conflates "do not invent law" with "do not
think". Those are separable: reasoning about the user's own facts using retrieved
rules is not filling a gap from memory.

## Non-goals

- **Matching a frontier model's reasoning.** A 5.5 GB local model will not. The trade
  is offline and private; we close part of the gap, not all of it.
- **Enumerating scenarios.** Real situations are unbounded. Nothing here is tested by
  adding more hand-written cases; see Testing.
- **Predicting outcomes.** Rule 6 stands. Map evidence to criteria, never forecast an
  adjudication.

## Design

### A. Conversation state

Carry the last N turns (question + the answer's conclusions, not its full prose) into
both the prompt and the retrieval query, so a follow-up composes on what came before
instead of resetting. Turns are held in memory for the session; nothing is written to
disk except via B.

### B. A situation model that persists

Durable facts the user states in conversation — "I co-founded it and control it", "my
degree is computer science" — are extracted, shown to the user, and stored under the
existing `~/.visa/me/` substrate that `visa remember` already writes to. The user can
see and correct them (`/facts`). This is what turns a session into a journey: the
second conversation starts knowing what the first established.

Facts are the user's assertions, never the model's inferences. An inference belongs in
an answer, not in the stored profile.

### C. Rewrite SYSTEM to separate two rules

Unchanged and absolute: **law comes only from the sources.** Never invent a provision,
cite only by bracket number, respect precedence.

New and required: **apply that law to this person.** Say which rules govern them given
their facts, what follows, and what remains undetermined. A correct answer that does
not connect to the person's circumstances is a failed answer.

### D. Answer, mark the contingency, then ask

When the outcome turns on a fact not in evidence, the model answers with what it has,
marks explicitly which parts depend on the unknown, and closes with **exactly one**
question that would most sharpen the answer. Never both branches enumerated; never a
question instead of an answer.

### E. Flag unsupported conclusions

`verify_citations` catches fabricated citations. Nothing validates reasoning — the
worst error observed in testing ("lacking association membership would likely lead to a
denial", which is flatly wrong) drew zero flags. Adding reasoning widens this exposure.

Mitigation: every normative conclusion must hang off a bracketed source. Conclusions
without one are surfaced in the existing answer-check panel. This does not catch bad
inference; it makes unsupported assertion visible, which is the achievable goal.

"Normative conclusion" needs an operational definition or E cannot be implemented or
tested. Use: a sentence containing deontic or eligibility language — *must, may, cannot,
is required, is barred, is eligible, is not eligible, qualifies, does not qualify* —
that is not itself inside a quotation. Deliberately over-inclusive: a false flag costs
the user a glance at the panel, a missed one costs them a wrong belief about their
status.

## Staging

A–E is more than one implementation plan should carry. It stages cleanly, and each
stage is independently useful:

1. **C + D** — the prompt rewrite and answer-then-ask. No new state, no storage, one
   file. This alone changes the behaviour that prompted the work.
2. **A** — conversation state. Makes follow-ups compose; needs the budget guard.
3. **E** — unsupported-conclusion flagging. Independent of the others.
4. **B** — the persisted situation model. Largest, touches storage and the share
   boundary, and benefits from seeing how 1–3 behave first.

Ship 1 before designing 4 in detail.

## Prompt budget

Sources already run ~8.3k tokens against `num_ctx` 16384. Conversation state and the
facts block are additive and must be budgeted in `build_prompt`, which already trims
sources against the remaining allowance.

Ordering, per finding 0: generation keeps the *tail*, so the sequence stays
SYSTEM → SOURCES → conversation → facts/deadlines → question. Conversation goes above
the facts block, never below it. **Anything added here must be budgeted, or it will
silently push the deadline block out of context and reproduce the original bug.**

## Testing

Scenario tests cannot cover this and adding more is the wrong instrument. Test the
*properties* of the interaction instead:

- **Statefulness:** a follow-up containing an anaphor ("what about that?") must produce
  a prompt containing the prior turn's subject.
- **Application:** given a profile fact and a retrieved rule conditioned on that fact,
  the answer must reference the fact. Asserted structurally, not by expected wording.
- **Answer-then-ask:** when a rule in the sources is conditioned on a fact absent from
  the situation model, the answer ends with exactly one question. When no such gap
  exists, it ends with none.
- **Grounding:** no normative conclusion without a bracket.
- **Budget:** with maximum conversation state and a full source slate, the deadline
  block still survives into the prompt — the finding-0 guard, extended.

## Risks

1. **A 7B asked to reason will be confidently wrong more often.** E is the mitigation
   and it is partial. The `/sources N` habit and the closer stay load-bearing.
2. **Fact extraction can mis-capture.** Facts are shown and correctable; the model's
   inferences never enter storage.
3. **Prompt budget regression** — see above; guarded by test.
4. **Latency.** Conversation state adds prefill, which is already ~89% of the wait.
   Summarised conclusions rather than full prose keep this bounded.

## Open questions

- How many turns of history, and summarised how?
- Does fact extraction run every turn (latency) or on an explicit `/remember`?
- Should the situation model be shown once at session start, or only on `/facts`?
