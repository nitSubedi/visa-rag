"""Build the local law library: download each source, chunk it, embed it, store it.

The one step that needs the network, run on the person's own machine (the desktop app's
first launch, or `visa init`). Reports progress through a callback so a window can show
it; no printing here.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from . import chunk, embed, fetch, profile, store
from .config import settings
from .sources import Source, load_defs

Progress = Callable[[str, float], None]  # (what is happening, 0..1 overall)


def _quiet(_: str, __: float) -> None:
    return None


@dataclass
class Built:
    chunks: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (source, error)


def is_built() -> bool:
    return any((s.dir / "vectors.npy").exists() for s in load_defs())


def index_source(src: Source, progress: Progress = _quiet, refetch: bool = True) -> int:
    if refetch and src.all_urls:
        fetch.fetch(src, progress=lambda m: progress(f"{src.title}: {m}", -1))
    rows = chunk.chunk_source(src)
    if not rows:
        return 0
    n = len(rows)

    def step(done: int, total: int) -> None:
        progress(
            f"{src.title}: reading passage {done:,} of {total:,}", done / max(1, total)
        )

    vecs = embed.embed([r.embed_text or r.text for r in rows], progress=step)
    if not (src.dir / "manifest.json").exists():
        src.dir.mkdir(parents=True, exist_ok=True)
        (src.dir / "manifest.json").write_text(
            json.dumps(
                {
                    "slug": src.slug,
                    "title": src.title,
                    "tier": src.tier,
                    "kind": src.kind,
                    "fetched": dt.date.today().isoformat(),
                    "refresh_days": src.refresh_days,
                    "note": src.note,
                    "files": [],
                },
                indent=2,
            )
        )
    extra: dict[str, object] = {"private": src.private} if src.private else {}
    store.write_shard(src.dir, rows, vecs, extra)
    return n


def build(progress: Progress = _quiet, only: str | None = None) -> Built:
    """Index every corpus source. A failed source is recorded, not fatal, and the
    caller must say the library is incomplete."""
    settings.ensure_dirs()
    profile.init()
    defs = [s for s in load_defs() if not only or s.slug == only]
    out = Built()
    for i, s in enumerate(defs):
        base, width = i / len(defs), 1 / len(defs)

        def scaled(
            msg: str, frac: float, base: float = base, width: float = width
        ) -> None:
            progress(msg, base + width * frac if frac >= 0 else base)

        try:
            out.chunks += index_source(s, scaled)
        except Exception as e:
            out.failed.append((s.slug, str(e)))
    progress("done", 1.0)
    return out
