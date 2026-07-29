"""Shard storage: chunks.parquet + vectors.npy + manifest.json.

Deliberately not a vector database. At ~4k chunks an exact brute-force matmul takes
~3ms, so an ANN index would trade correctness for a speedup we do not need. Two plain
files also means no format lock-in and a shard you can email.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import config

COLUMNS = ["id", "text", "citation", "title", "path", "url",
           "shard", "tier", "kind", "source_title"]


def write_shard(dirpath: Path, rows: list[dict], vectors: np.ndarray,
                extra_manifest: dict | None = None) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    tbl = pa.table({c: pa.array([r.get(c) for r in rows]) for c in COLUMNS})
    pq.write_table(tbl, dirpath / "chunks.parquet", compression="zstd")
    np.save(dirpath / "vectors.npy", vectors.astype(np.float32))

    mpath = dirpath / "manifest.json"
    man = json.loads(mpath.read_text()) if mpath.exists() else {}
    man.update({
        "chunks": len(rows),
        "embed_model": config.EMBED_MODEL,
        "embed_dim": int(vectors.shape[1]) if vectors.size else 0,
        "chunk_tokens": config.CHUNK_TOKENS,
    })
    man.update(extra_manifest or {})
    mpath.write_text(json.dumps(man, indent=2))


class Shard:
    def __init__(self, dirpath: Path):
        self.dir = Path(dirpath)
        self.manifest = json.loads((self.dir / "manifest.json").read_text())
        self.table = pq.read_table(self.dir / "chunks.parquet")
        self.vectors = np.load(self.dir / "vectors.npy")
        self.rows = self.table.to_pylist()

    @property
    def slug(self) -> str:
        return self.manifest.get("slug", self.dir.name)

    @property
    def private(self) -> bool:
        return bool(self.manifest.get("private")) or self.manifest.get("tier") == 9

    def check_embed_model(self) -> str | None:
        """A shard built with a different embedding model yields meaningless
        neighbours. Refuse rather than silently degrade."""
        got = self.manifest.get("embed_model")
        if got and got != config.EMBED_MODEL:
            return (f"shard '{self.slug}' was indexed with '{got}' but the configured "
                    f"model is '{config.EMBED_MODEL}' — re-run `visa index {self.slug}`")
        return None

    def __len__(self) -> int:
        return len(self.rows)


def load_shards(include_private: bool = True) -> list[Shard]:
    out = []
    roots = [config.CORPUS]
    if include_private:
        roots.append(config.ME)
    for root in roots:
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if (d / "chunks.parquet").exists() and (d / "manifest.json").exists():
                try:
                    out.append(Shard(d))
                except Exception:
                    continue
    return out
