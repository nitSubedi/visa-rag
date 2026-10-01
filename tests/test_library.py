"""The first build: from documents shipped with the app, never half-done silently."""

from __future__ import annotations

import pytest

from visa import library
from visa.config import settings
from visa.sources import load_defs


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "home", tmp_path / "home")
    seed = tmp_path / "res" / "seed"
    monkeypatch.setattr(library, "RESOURCES", tmp_path / "res")
    return seed


def test_a_partial_library_is_not_built(home) -> None:
    slugs = [s.slug for s in load_defs()]
    first = next(s for s in load_defs())
    first.dir.mkdir(parents=True)
    (first.dir / "vectors.npy").write_bytes(b"x")
    assert not library.is_built()
    assert library.missing() == [s for s in slugs if s != first.slug]


def test_shipped_documents_are_used_without_the_network(home) -> None:
    src = next(s for s in load_defs())
    (home / src.slug / "raw").mkdir(parents=True)
    (home / src.slug / "raw" / "doc.xml").write_text("<x/>")
    (home / src.slug / "manifest.json").write_text(f'{{"slug": "{src.slug}"}}')
    assert library._seed(src)
    assert (src.dir / "raw" / "doc.xml").read_text() == "<x/>"
    assert (src.dir / "manifest.json").exists()
    assert not library._seed(src)  # already there: never overwritten


def test_no_seed_falls_back_to_downloading(home) -> None:
    assert not library._seed(next(s for s in load_defs()))
