"""Build the local law library: download each source, chunk it, embed it, store it.

The one step that needs the network, run on the person's own machine (the desktop app's
first launch, or `visa init`). Reports progress through a callback so a window can show
it; no printing here.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from . import chunk, embed, fetch, profile, store
from .config import RESOURCES, settings
from .sources import Source, load_defs

Progress = Callable[[str, float], None]  # (what is happening, 0..1 overall)


def _quiet(_: str, __: float) -> None:
    return None


@dataclass
class Built:
    chunks: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (source, error)


def missing() -> list[str]:
    """Corpus sources not yet indexed. A partial library is not a built one: counting
    "any source" as built hid the retry after the statute's site was down."""
    return [s.slug for s in load_defs() if not (s.dir / "vectors.npy").exists()]


def is_built() -> bool:
    return not missing()


def _seed(src: Source) -> bool:
    """Copy the source documents the app ships with (raw files + their manifest, with
    each file's SHA-256 and edition date) into the library. The first build then needs
    no network: on the day the packaged app was first tested end to end,
    uscode.house.gov was down for maintenance and the statute could not be fetched."""
    seed = RESOURCES / "seed" / src.slug
    if not (seed / "raw").is_dir() or any((src.dir / "raw").glob("*")):
        return False
    src.dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(seed / "raw", src.dir / "raw", dirs_exist_ok=True)
    if (seed / "manifest.json").exists():
        shutil.copy2(seed / "manifest.json", src.dir / "manifest.json")
    return True


def index_source(src: Source, progress: Progress = _quiet, refetch: bool = True) -> int:
    if _seed(src):
        progress(f"{src.title}: using the copy shipped with the app", -1)
    elif refetch and src.all_urls:
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
    todo = set(missing()) if only is None else {only}
    defs = [s for s in load_defs() if s.slug in todo]
    out = Built()
    for i, s in enumerate(defs):
        base, width = i / max(1, len(defs)), 1 / max(1, len(defs))

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
