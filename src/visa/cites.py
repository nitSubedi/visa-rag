"""What a section number looks like — shared, because four hand-written copies of
`[\\d.]+` each broke on the same thing.

8 CFR has lettered parts (213a, 245a, 274a: 448 chunks) and 8 U.S.C. lettered and
hyphenated sections (1324a, 1440-1: 267 chunks). `[\\d.]+` read "274a.12" as "274", so
the injunction register could not match 274a.12 at all and served an enjoined
employment rule as law, and every 274a section shared one per-section cap.
"""

from __future__ import annotations

CFR_SECTION = r"\d+[a-z]?\.\d+"  # 214.2, 274a.12
USC_SECTION = r"\d+[a-z]?(?:[\u2013-]\d+)?"  # 1101, 1324a, 1440-1 (en dash in the corpus)
SECTION_NO = rf"(?:{CFR_SECTION}|{USC_SECTION})"
