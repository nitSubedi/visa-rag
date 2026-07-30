"""Payload schemas. Pydantic is the source of truth for chunk and manifest shape."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """One retrievable passage, carrying the citation it came from.

    The citation is not decoration: an answer that cannot point at a provision is
    indistinguishable from one the model invented.
    """

    id: str = ""
    text: str
    citation: str
    title: str = ""
    path: str = ""
    url: str = ""
    shard: str
    tier: int
    kind: str = ""
    source_title: str = ""


class SourceFile(BaseModel):
    """A fetched file, hashed so a recipient can verify it against the .gov original."""

    name: str
    url: str
    sha256: str
    bytes: int


class Manifest(BaseModel):
    """Shard provenance: what it is, where it came from, and how current it is."""

    slug: str
    title: str = ""
    tier: int = 9
    kind: str = ""
    jurisdiction: str = "US-federal"
    fetched: dt.date
    refresh_days: int = 30
    note: str = ""
    private: bool = False
    files: list[SourceFile] = Field(default_factory=list)

    # Index provenance — a shard embedded with a different model yields meaningless
    # neighbours, so this is checked before use rather than trusted.
    chunks: int = 0
    embed_model: str = ""
    embed_dim: int = 0
    chunk_tokens: int = 0

    # eCFR only: when the law was last amended matters more than when we downloaded it.
    amended_on: dt.date | None = None
    edition: dt.date | None = None
    upstream_current_as_of: dt.date | None = None

    @property
    def age_days(self) -> int:
        return (dt.date.today() - self.fetched).days

    @property
    def is_stale(self) -> bool:
        return self.age_days > self.refresh_days
