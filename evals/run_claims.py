"""Score a claim-support checker against hand labels (STATE.md finding 28).

evals/claims/ holds every sentence of the 16 held-out answers from 2026-09-29 (188),
the passages the model actually had for each (post-redaction), and a label per sentence
assigned by reading it against those passages:

  S  supported by the sources, the notice, or the computed facts (incl. arithmetic)
  U  unsupported: invented, misapplied, contradicted
  M  true content cited to the wrong source
  N  not a claim (a question, restated facts, "the sources do not address ...")
  X  could not be verified from what was shown — excluded, not guessed

A checker flags sentences; the score is recall and precision on U against S. Run:

    python evals/run_claims.py lexical
    python evals/run_claims.py ollama:gemma3:4b
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
D = REPO / "evals" / "claims"

STOP = {
    *[
        "the",
        "a",
        "an",
        "of",
        "to",
        "and",
        "or",
        "in",
        "on",
        "for",
        "is",
        "are",
        "be",
        "by",
        "with",
        "as",
        "that",
        "this",
        "it",
        "its",
    ],
    *["at", "from", "your", "you", "their", "they", "not", "may", "must", "can", "will"],
}


def words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", t.lower()) if w not in STOP and len(w) > 2}


def load() -> list[dict[str, object]]:
    """One row per S/U/M sentence: the claim, its passages, the facts, the label."""
    claims = json.loads((D / "claims.json").read_text())
    labels = json.loads((D / "labels.json").read_text())
    per: dict[str, list[dict[str, object]]] = {}
    for x in claims:
        per.setdefault(x["slug"], []).append(x)
    rows = []
    for slug, xs in per.items():
        sl = json.loads((D / f"slate_{slug}.json").read_text())
        srcs = sl["sources"]
        facts = sl["tail"].split("QUESTION:")[0][-2000:]
        for x, lab in zip(xs, labels[slug].split(), strict=True):
            if lab not in "SUM":
                continue
            claim = re.sub(r"\[[\d,\s]+\]|\[n\]", "", x["sent"]).strip()
            cited = [n for n in x["cites"] if 1 <= n <= len(srcs)]

            def overlap(n: int, claim: str = claim, srcs: list = srcs) -> int:  # type: ignore[type-arg]
                return len(words(claim) & words(srcs[n - 1][1]))

            pick = cited or sorted(range(1, len(srcs) + 1), key=lambda n: -overlap(n))[:3]
            rows.append(
                {
                    "slug": slug,
                    "label": lab,
                    "claim": claim,
                    "passages": [srcs[n - 1] for n in pick],
                    "facts": facts,
                }
            )
    return rows


def lexical(row: dict[str, object]) -> bool:
    """Flag when no single passage sentence covers 60% of the claim's content words."""
    w = words(str(row["claim"]))
    texts = [t for _, t in row["passages"]] + [str(row["facts"])]  # type: ignore[union-attr]
    sents = [s for t in texts for s in re.split(r"(?<=[.;:])\s+", t)]
    return max((len(w & words(s)) / max(1, len(w)) for s in sents), default=0) < 0.6


def ollama_judge(model: str) -> Callable[[dict[str, object]], bool]:
    import os

    os.environ["VISA_CHAT_MODEL"] = model
    from visa import answer

    schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["supported", "not_supported"]}
        },
        "required": ["verdict"],
    }

    def judge(row: dict[str, object]) -> bool:
        passages = "\n\n".join(
            f"PASSAGE ({c}):\n{t[:2500]}"
            for c, t in row["passages"]  # type: ignore[union-attr]
        )
        prompt = (
            f"{passages}\n\nFACTS AND NOTES:\n{row['facts']}\n\nCLAIM: {row['claim']}\n\n"
            "Do the passages or the facts state or directly imply this claim? A claim "
            "that applies a rule to a situation the passage does not describe, adds a "
            "detail the passages do not contain, or contradicts them is not_supported."
        )
        raw = "".join(
            answer.stream_chat(
                [{"role": "user", "content": prompt}],
                model=model,
                temperature=0.0,
                fmt=schema,
                max_tokens=30,
            )
        )
        try:
            return bool(json.loads(raw)["verdict"] == "not_supported")
        except Exception:
            return True

    return judge


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "lexical"
    flag = lexical if which == "lexical" else ollama_judge(which.split(":", 1)[1])
    rows = load()
    res = [(r, flag(r)) for r in rows]
    su = [(r, f) for r, f in res if r["label"] in "SU"]
    tp = sum(f and r["label"] == "U" for r, f in su)
    fp = sum(f and r["label"] == "S" for r, f in su)
    u = sum(r["label"] == "U" for r, _ in su)
    m = [(r, f) for r, f in res if r["label"] == "M"]
    print(f"{which}: flags {tp + fp}  recall {tp}/{u}  precision {tp}/{max(1, tp + fp)}")
    print(f"  miscited flagged {sum(f for _, f in m)}/{len(m)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
