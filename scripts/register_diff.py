"""Show exactly which CFR paragraphs changed between two eCFR editions.

For building injunction-register entries. A court enjoins a *rule*; the register needs
the *paragraphs that rule wrote*. Hand-typing those paths is how 214.2(f)(11) — the whole
OPT and STEM application paragraph — got registered as enjoined when the rule rewrote
one sub-paragraph of it, withholding the in-force E-Verify and unemployment rules.

This proposes; it never writes. Deciding which changes belong to the enjoined rule (and
not to some other rule published in the same window) is a person's call.

    python scripts/register_diff.py OLD.xml NEW.xml 214.1 214.2:f,i,j [--full]

--full prints whole paragraphs, which the register needs to redact them precisely.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from visa.chunk import ecfr_paragraphs
from visa.register import fingerprint


def main() -> None:
    args = sys.argv[1:]
    full = "--full" in args
    old_path, new_path, *targets = [a for a in args if a != "--full"]
    old, new = ecfr_paragraphs(Path(old_path)), ecfr_paragraphs(Path(new_path))
    for target in targets:
        sec, _, letters = target.partition(":")
        wanted = set(letters.split(",")) if letters else None
        before = {t for _, ps in old.get(sec, []) for t in ps}
        for letter, ps in new.get(sec, []):
            if wanted is not None and letter not in wanted:
                continue
            changed = [t for t in ps if t not in before]
            if not changed:
                continue
            n = len(changed)
            print(f"\n# 8 CFR {sec}({letter}) — {n} new or rewritten paragraph(s)")
            for t in changed:
                body = " ".join(t.split()).replace("\\", "\\\\").replace('"', '\\"')
                print(f'  "{body}",' if full else f'  "{fingerprint(t)}",')


if __name__ == "__main__":
    main()
