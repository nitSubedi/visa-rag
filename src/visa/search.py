"""Hybrid retrieval: BM25 fused with dense vectors.

Pure embeddings retrieve poorly on exact citations ("214.2(f)(10)") and terms of art
("preponderance of the evidence"), which is most of what legal lookup actually asks
for. BM25 covers that; vectors cover paraphrase. Reciprocal-rank fusion combines them
without needing calibrated scores.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from . import register, terminology
from .config import settings, tier_weight
from .embed import embed_query
from .models import Chunk
from .store import Shard, load_shards

# Keeps "214.2", "204.5(h)(3)" and "(f)" intact — the tokens that matter most here.
TOKEN = re.compile(r"[A-Za-z]+|\d+(?:\.\d+)*|\([a-z0-9]{1,4}\)")
STOP = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "in",
    "for",
    "and",
    "or",
    "is",
    "are",
    "be",
    "as",
    "by",
    "on",
    "that",
    "this",
    "with",
    "it",
    "at",
    "from",
    "may",
    "can",
    "i",
    "my",
    "me",
    "do",
    "does",
    "if",
    "what",
    "how",
    "when",
    "which",
}


def tokenize(s: str) -> list[str]:
    return [t for t in (x.lower() for x in TOKEN.findall(s)) if t not in STOP]


# A citation is a subsection; the unit a reader would call "the same law" is the
# section. Capping only by citation lets one section arrive in pieces.
SECTION = re.compile(r"^(\d+ (?:CFR|U\.S\.C\.) § [\d.]+)")
PM_CHAPTER = re.compile(r"^(USCIS PM Vol \S+, Pt \S+, Ch \S+?)(?:,|$)")


def section_key(citation: str) -> str:
    """The section a citation belongs to, or the citation itself when it has no
    subsection structure (Policy Manual chapters are already the right unit)."""
    m = SECTION.match(citation.strip())
    if m:
        return m.group(1)
    # A Policy Manual section ("…, Ch 5, D.3") belongs to its chapter. Without this a
    # chapter could fill the slate one sub-section at a time — the 204.12 crowding bug.
    pm = PM_CHAPTER.match(citation.strip())
    return pm.group(1) if pm else citation.strip()


@dataclass
class Hit:
    row: Chunk
    score: float
    cosine: float
    shard: str

    @property
    def tier(self) -> int:
        return self.row.tier


class Index:
    """All shards concatenated. Adding a shard costs nothing but a reload."""

    def __init__(self, shards: list[Shard]) -> None:
        self.shards = shards
        self.rows: list[Chunk] = []
        self.warnings: list[str] = []
        vecs = []
        for s in shards:
            w = s.check_embed_model()
            if w:
                self.warnings.append(w)
                continue
            self.rows.extend(s.rows)
            vecs.append(s.vectors)
        self.vectors = np.vstack(vecs) if vecs else np.zeros((0, 768), dtype=np.float32)
        self._build_bm25()

    @classmethod
    def load(cls, include_private: bool = True) -> Index:
        return cls(load_shards(include_private=include_private))

    def _build_bm25(self) -> None:
        self.df: Counter[str] = Counter()
        self.tf: list[Counter[str]] = []
        self.lens: list[int] = []
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(self.rows):
            toks = tokenize(r.text)
            c = Counter(toks)
            self.tf.append(c)
            self.lens.append(max(len(toks), 1))
            for t in c:
                self.df[t] += 1
                self.postings[t].append(i)
        self.avglen = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        self.N = max(len(self.rows), 1)

    def _bm25(self, query: str, k1: float = 1.5, b: float = 0.75) -> dict[int, float]:
        scores: dict[int, float] = defaultdict(float)
        for t in set(tokenize(query)):
            if t not in self.postings:
                continue
            idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i in self.postings[t]:
                f = self.tf[i][t]
                denom = f + k1 * (1 - b + b * self.lens[i] / self.avglen)
                scores[i] += idf * (f * (k1 + 1)) / denom
        return scores

    def search(
        self,
        query: str,
        k: int | None = None,
        shards: list[str] | None = None,
        rrf_k: int = 60,
        exclude: tuple[str, ...] = (),
    ) -> list[Hit]:
        k = k or settings.top_k
        if not self.rows:
            return []

        qv = embed_query(query)
        cos = self.vectors @ qv  # exact, ~3ms at this scale

        dense_rank = np.argsort(-cos)[: max(k * 8, 80)]
        lex = self._bm25(query)
        lex_rank = sorted(lex, key=lambda i: lex[i], reverse=True)[: max(k * 8, 80)]

        fused: dict[int, float] = defaultdict(float)
        for r, i in enumerate(dense_rank):
            fused[int(i)] += 1.0 / (rrf_k + r + 1)
        for r, i in enumerate(lex_rank):
            fused[int(i)] += 1.0 / (rrf_k + r + 1)

        hits = []
        for i, s in fused.items():
            row = self.rows[i]
            if shards and row.shard not in shards:
                continue
            # A sibling classification the question did not ask about. See
            # terminology.SCOPES.
            if exclude and any(terminology.under(row.citation, p) for p in exclude):
                continue
            hits.append(
                Hit(
                    row=row,
                    score=s * tier_weight(row.tier),
                    cosine=float(cos[i]),
                    shard=row.shard,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)

        # Cap per citation so one sprawling subsection cannot crowd out the evidence,
        # and per section so one law cannot do the same thing in pieces.
        seen: Counter[str] = Counter()
        sections: Counter[str] = Counter()
        kept = []
        usable = 0
        for h in hits:
            c = h.row.citation
            sec = section_key(c)
            if seen[c] >= settings.per_citation_cap:
                continue
            if sections[sec] >= settings.per_section_cap:
                continue
            seen[c] += 1
            sections[sec] += 1
            kept.append(h)
            # A suspended passage is returned — the banner and `/sources N` need it —
            # but its text is withheld from the prompt, so charging it a slot shrinks
            # the evidence. Measured: an OPT filing question spent 4 of 12 slots on
            # withheld passages and lost two checks; the scenario that withheld nothing
            # lost none.
            if not register.suspended(h.row.citation, h.row.text):
                usable += 1
            if usable >= k:
                break
        return kept

    def search_many(
        self, queries: list[str], k: int | None = None, exclude: tuple[str, ...] = ()
    ) -> list[Hit]:
        """Retrieve for the question plus its issues and merge.

        `queries[0]` is the user's own words; the rest are spotted issues. A plain
        union lets one broad issue dominate, which reproduces the failure
        decomposition exists to fix — but pure round-robin has the opposite bug, and
        it is the one that actually bit. With six issues the question itself got a
        seventh of the budget, so a founder asking about life after OPT received
        L-1 and EB-5 passages while the SEVP guidance that decides the case — the
        self-employment bar — was crowded out. Measured: 6 of 12 slots on a plain
        search, 1 of 12 after decomposition.

        So the question keeps a reserved floor of the budget and the issues
        interleave for the remainder. Issues still surface provisions the person did
        not know to ask about; they can no longer outvote what they did ask.
        """
        k = k or settings.top_k
        # Each issue retrieves a full slate. Deduplication across issues is heavy —
        # they overlap by design — so retrieving only k/n per issue starves the merge
        # and yields fewer sources than a single pass.
        ranked = [self.search(q, k=k, exclude=exclude) for q in queries]

        merged: list[Hit] = []
        seen: set[str] = set()
        per_citation: Counter[str] = Counter()
        per_section: Counter[str] = Counter()

        # A suspended passage is still returned — the banner and `/sources N` need it —
        # but its text is withheld from the prompt, so it must not count toward the
        # budget. `usable` is what the model can actually read, and every round below
        # measures progress by it rather than by len(merged).
        usable = 0

        def take(hit: Hit, citation_cap: int) -> bool:
            nonlocal usable
            key = f"{hit.row.citation}|{hit.row.text[:80]}"
            sec = section_key(hit.row.citation)
            if key in seen or per_citation[hit.row.citation] >= citation_cap:
                return False
            if per_section[sec] >= settings.per_section_cap:
                return False
            seen.add(key)
            per_citation[hit.row.citation] += 1
            per_section[sec] += 1
            merged.append(hit)
            if not register.suspended(hit.row.citation, hit.row.text):
                usable += 1
            return True

        cap = settings.per_citation_cap

        # Round 0: the question's own best passages, up to its reserved floor.
        floor = min(k, max(1, round(k * settings.primary_share))) if ranked else 0
        for hit in ranked[0] if ranked else []:
            if usable >= floor:
                break
            take(hit, cap)

        # Round 1: interleave so every issue is represented before any repeats,
        # capped per provision so one broad chapter cannot swallow the budget.
        for rank in range(k):
            for hits in ranked:
                if rank < len(hits) and take(hits[rank], cap) and usable >= k:
                    return merged

        # Round 2: top up without the diversity cap rather than return short — an
        # earlier version returned *fewer* sources than a single pass.
        for hits in ranked:
            for hit in hits:
                if take(hit, k) and usable >= k:
                    return merged
        return merged


def passes_gate(hits: list[Hit]) -> bool:
    """The citation gate: without at least one solidly-matching passage the tool must
    decline rather than answer from model memory."""
    return bool(hits) and max(h.cosine for h in hits) >= settings.min_score
