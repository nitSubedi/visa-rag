"""Score the held-out set. Reports two views, because they measure different things:

  prose  the model's answer alone — whether the model does its job
  shown  what the CLI prints: warnings + answer + the COMPUTED DEADLINES block —
         whether the user ends up with the right information

and a per-category breakdown, which is the point of the set: the diagnosis under test is
that failures concentrate in derive and restate. Every answer is saved for reading.

    python evals/run_heldout.py --model gemma3:4b --repeat 2 --out heldout.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "evals"))


def _isolated_home(profile: dict[str, object]) -> Path:
    home = Path(tempfile.mkdtemp(prefix="visa-heldout-"))
    (home / "me").mkdir(parents=True)
    (home / "corpus").symlink_to(Path.home() / ".visa" / "corpus")
    lines = [
        f"{k} = {str(v).lower()}" if isinstance(v, bool) else f'{k} = "{v}"'
        for k, v in profile.items()
    ]
    (home / "me" / "profile.toml").write_text("\n".join(lines) + "\n")
    return home


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out")
    args = ap.parse_args()
    if args.model:
        os.environ["VISA_CHAT_MODEL"] = args.model

    from heldout import HELDOUT

    per_cat = {v: defaultdict(lambda: [0, 0]) for v in ("prose", "shown")}
    totals = {"prose": [0, 0], "shown": [0, 0]}
    saved: dict[str, list[dict[str, object]]] = {}

    for sc in HELDOUT:
        home = _isolated_home(sc.profile)
        os.environ["VISA_HOME"] = str(home)
        for mod in [m for m in list(sys.modules) if m.startswith("visa")]:
            del sys.modules[mod]
        from visa import answer as ans
        from visa import dates, profile
        from visa.search import Index

        idx = Index.load()
        cells: dict[str, dict[str, int]] = {"prose": {}, "shown": {}}
        for _ in range(args.repeat):
            hits, gated, warnings = ans.answer(sc.question, idx)
            prose = ""
            if gated:
                prose = "".join(
                    ans.stream_chat(ans.build_prompt(sc.question, hits), temperature=0.0)
                )
            shown = "\n".join([*warnings, prose, dates.render(profile.load())])
            saved.setdefault(sc.slug, []).append(
                {"gated": gated, "warnings": warnings, "prose": prose}
            )
            for view, text in (("prose", prose), ("shown", shown)):
                for c in sc.checks:
                    found = bool(re.search(c.pattern, text, re.I | re.S))
                    ok = found if c.kind == "must" else not found
                    cells[view][c.name] = cells[view].get(c.name, 0) + ok

        line = []
        for view in ("prose", "shown"):
            got = 0
            for c in sc.checks:
                n = cells[view][c.name]
                per_cat[view][c.category][0] += n
                per_cat[view][c.category][1] += args.repeat
                got += n
            totals[view][0] += got
            totals[view][1] += len(sc.checks) * args.repeat
            line.append(f"{view} {got}/{len(sc.checks) * args.repeat}")
        missed = [c.name for c in sc.checks if cells["prose"][c.name] < args.repeat]
        print(f"{sc.slug:26} {'  '.join(line)}   prose missed: {missed}", flush=True)
        shutil.rmtree(home, ignore_errors=True)

    model = os.environ.get("VISA_CHAT_MODEL", "default")
    print(f"\n{model}")
    for view in ("prose", "shown"):
        p, n = totals[view]
        print(f"  {view:6} {p}/{n} ({100 * p // max(n, 1)}%)")
    print("\n  by category (prose / shown):")
    for cat in ("copy", "derive", "restate", "attribute", "behave"):
        a, b = per_cat["prose"][cat], per_cat["shown"][cat]
        if b[1]:
            print(
                f"    {cat:10} {a[0]:>3}/{a[1]:<3} ({100 * a[0] // a[1]:3}%)   "
                f"{b[0]:>3}/{b[1]:<3} ({100 * b[0] // b[1]:3}%)"
            )
    if args.out:
        Path(args.out).write_text(json.dumps(saved, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
