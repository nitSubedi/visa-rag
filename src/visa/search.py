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
            hits.append(
                Hit(
                    row=row,
                    score=s * tier_weight(row.tier),
                    cosine=float(cos[i]),
                    shard=row.shard,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)

        # Keep at most 3 chunks per citation so one sprawling section cannot crowd out
        # the rest of the evidence.
        seen: Counter[str] = Counter()
        kept = []
        for h in hits:
            c = h.row.citation
            if seen[c] >= 3:
                continue
            seen[c] += 1
            kept.append(h)
            if len(kept) >= k:
                break
        return kept

    def search_many(self, queries: list[str], k: int | None = None) -> list[Hit]:
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
        ranked = [self.search(q, k=k) for q in queries]

        merged: list[Hit] = []
        seen: set[str] = set()
        per_citation: Counter[str] = Counter()

        def take(hit: Hit, citation_cap: int) -> bool:
            key = f"{hit.row.citation}|{hit.row.text[:80]}"
            if key in seen or per_citation[hit.row.citation] >= citation_cap:
                return False
            seen.add(key)
            per_citation[hit.row.citation] += 1
            merged.append(hit)
            return True

        cap = settings.per_citation_cap

        # Round 0: the question's own best passages, up to its reserved floor.
        floor = min(k, max(1, round(k * settings.primary_share))) if ranked else 0
        for hit in ranked[0] if ranked else []:
            if len(merged) >= floor:
                break
            take(hit, cap)

        # Round 1: interleave so every issue is represented before any repeats,
        # capped per provision so one broad chapter cannot swallow the budget.
        for rank in range(k):
            for hits in ranked:
                if rank < len(hits) and take(hits[rank], cap) and len(merged) >= k:
                    return merged

        # Round 2: top up without the diversity cap rather than return short — an
        # earlier version returned *fewer* sources than a single pass.
        for hits in ranked:
            for hit in hits:
                if take(hit, k) and len(merged) >= k:
                    return merged
        return merged


def passes_gate(hits: list[Hit]) -> bool:
    """The citation gate: without at least one solidly-matching passage the tool must
    decline rather than answer from model memory."""
    return bool(hits) and max(h.cosine for h in hits) >= settings.min_score
