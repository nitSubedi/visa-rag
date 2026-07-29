"""Declarative source registry.

Adding a corpus = drop a TOML file in sources/. A new parser is only needed when
the upstream format is genuinely novel.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass
class Source:
    slug: str
    title: str
    tier: int
    fetcher: str
    chunker: str
    url: str = ""
    urls: list[str] = field(default_factory=list)
    jurisdiction: str = "US-federal"
    kind: str = ""
    refresh_days: int = 30
    private: bool = False
    note: str = ""

    @property
    def dir(self) -> Path:
        return (config.ME / "shard") if self.private else (config.CORPUS / self.slug)

    @property
    def all_urls(self) -> list[str]:
        return ([self.url] if self.url else []) + list(self.urls)


def load_defs(path: Path | None = None) -> list[Source]:
    d = path or config.SOURCE_DEFS
    out = []
    for f in sorted(d.glob("*.toml")):
        raw = tomllib.loads(f.read_text())
        raw.setdefault("slug", f.stem)
        out.append(Source(**raw))
    return sorted(out, key=lambda s: s.tier)


def load_def(slug: str) -> Source | None:
    return next((s for s in load_defs() if s.slug == slug), None)
