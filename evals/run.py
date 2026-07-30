"""Score situational reasoning across models and retrieval strategies.

Usage:
    python evals/run.py                          # current settings
    python evals/run.py --model qwen3:14b        # compare a model
    python evals/run.py --strategy single        # compare retrieval
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "evals"))


def _isolated_home(profile: dict[str, object]) -> Path:
    """A throwaway VISA_HOME so evals never touch the real profile, sharing the
    corpus by symlink rather than re-downloading it."""
    home = Path(tempfile.mkdtemp(prefix="visa-eval-"))
    (home / "me").mkdir(parents=True)
    (home / "corpus").symlink_to(Path.home() / ".visa" / "corpus")
    lines = []
    for k, v in profile.items():
        lines.append(f"{k} = {str(v).lower()}" if isinstance(v, bool) else f'{k} = "{v}"')
    (home / "me" / "profile.toml").write_text("\n".join(lines) + "\n")
    return home


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--strategy", choices=["issues", "single"], default=None)
    ap.add_argument("--only")
    ap.add_argument(
        "--repeat", type=int, default=1, help="runs per scenario; scores are averaged"
    )
    args = ap.parse_args()

    from scenarios import SCENARIOS

    if args.model:
        os.environ["VISA_CHAT_MODEL"] = args.model
    if args.strategy:
        os.environ["VISA_RETRIEVAL"] = args.strategy

    total_score = total_max = 0
    rows: list[tuple[str, int, int, float, list[str]]] = []

    for sc in SCENARIOS:
        if args.only and sc.slug != args.only:
            continue
        home = _isolated_home(sc.profile)
        os.environ["VISA_HOME"] = str(home)

        for mod in [m for m in list(sys.modules) if m.startswith("visa")]:
            del sys.modules[mod]
        from visa import answer as ans
        from visa.search import Index

        idx = Index.load()
        t0 = time.time()
        # Temperature 0: an eval that cannot be reproduced measures nothing. Earlier
        # runs at 0.15 swung +/-1 per scenario, which is larger than the effects
        # being measured.
        tallies: dict[str, int] = {ck.name: 0 for ck in sc.checks}
        for _ in range(args.repeat):
            hits, gated, _ = ans.answer(sc.question, idx)
            text = ""
            if gated:
                text = "".join(
                    ans.stream_chat(ans.build_prompt(sc.question, hits), temperature=0.0)
                )
            for ck in sc.checks:
                found = bool(re.search(ck.pattern, text, re.I | re.S))
                if found if ck.kind == "must" else not found:
                    tallies[ck.name] += 1
        elapsed = time.time() - t0

        failed = [n for n, c in tallies.items() if c < args.repeat]
        score = round(sum(tallies.values()) / args.repeat)
        rows.append((sc.slug, score, len(sc.checks), elapsed, failed))
        total_score += score
        total_max += len(sc.checks)
        shutil.rmtree(home, ignore_errors=True)

        mark = "✓" if score == len(sc.checks) else "✗"
        print(
            f"{mark} {sc.slug:24} {score}/{len(sc.checks)}  {elapsed:5.1f}s"
            + (f"  missed: {', '.join(failed)}" if failed else "")
        )

    model = os.environ.get("VISA_CHAT_MODEL", "default")
    strat = os.environ.get("VISA_RETRIEVAL", "default")
    pct = 100 * total_score / total_max if total_max else 0
    print(
        f"\n{model} · {strat} · {total_score}/{total_max} ({pct:.0f}%) · "
        f"{sum(r[3] for r in rows):.0f}s total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
