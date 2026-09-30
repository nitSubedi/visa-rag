"""Runtime updates of public legal data: validated, never rolled back, never personal."""

from __future__ import annotations

from pathlib import Path

import pytest

from visa import bulletin, register, updates
from visa.config import settings

SHIPPED_BULLETIN = Path(__file__).parents[1] / "bulletin" / "visa_bulletin.toml"
SHIPPED_REGISTER = Path(__file__).parents[1] / "register" / "injunctions.toml"
URL = "https://example.org/visa_bulletin.toml"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "home", tmp_path)
    monkeypatch.setattr(settings, "bulletin_url", URL)
    monkeypatch.setattr(settings, "register_url", "")
    register.clear_cache()
    yield tmp_path
    register.clear_cache()


def serve(monkeypatch, body: bytes | Exception, seen: list | None = None) -> None:
    def fake(url: str) -> bytes:
        if seen is not None:
            seen.append(url)
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(updates, "_fetch", fake)


def table(month: str = "2026-11", cell: str = "2025-02-01", sources: int = 2) -> bytes:
    src = ", ".join(f'"S{i}"' for i in range(sources))
    return (
        f'month = "{month}"\nmethod = "consensus of independent republications"\n'
        f"sources = [{src}]\n"
        f'uscis_chart = "final_action"\nuscis_url = ""\n'
        f'[final_action."EB-2"]\nall = "{cell}"\n'
        f'[dates_for_filing."EB-2"]\nall = "C"\n'
    ).encode()


def result(name: str, force: bool = True) -> updates.Result:
    return next(r for r in updates.refresh_all(force=force) if r.name == name)


def bulletin_result(force: bool = True) -> updates.Result:
    return result("Visa Bulletin", force)


def test_no_url_uses_the_shipped_copy(home, monkeypatch) -> None:
    monkeypatch.setattr(settings, "bulletin_url", "")
    assert bulletin_result().status == "skipped"
    assert not (home / "bulletin").exists()


def test_a_valid_newer_table_replaces_the_shipped_one(home, monkeypatch) -> None:
    serve(monkeypatch, table())
    assert bulletin_result().status == "updated"
    b = bulletin.load()
    assert b is not None and b.label == "November 2026"


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (table(cell="15MAY"), "unreadable cell"),
        (table(sources=1), "at least two sources"),
        (table(month="2026-07"), "older than the local table"),  # shipped is 2026-10
        (b'month = "2026-11"\n', "no chart"),
    ],
)
def test_a_bad_download_is_refused(home, monkeypatch, body, why) -> None:
    serve(monkeypatch, body)
    r = bulletin_result()
    assert r.status == "kept" and why in r.detail
    assert not (home / "bulletin" / "visa_bulletin.toml").exists()


def test_offline_keeps_the_current_copy(home, monkeypatch) -> None:
    serve(monkeypatch, table())
    bulletin_result()
    serve(monkeypatch, OSError("offline"))
    r = bulletin_result()
    assert r.status == "kept" and "update failed" in r.detail
    b = bulletin.load()
    assert b is not None and b.label == "November 2026"


def test_checked_at_most_once_a_day(home, monkeypatch) -> None:
    seen: list[str] = []
    serve(monkeypatch, table(), seen)
    bulletin_result(force=True)
    assert bulletin_result(force=False).status == "unchanged"
    assert len(seen) == 1


def test_the_request_is_only_the_configured_url(home, monkeypatch) -> None:
    """Nothing about the person leaves: no query string, the profile untouched."""
    seen: list[str] = []
    serve(monkeypatch, table(), seen)
    (home / "me").mkdir()
    (home / "me" / "profile.toml").write_text('country_of_birth = "India"\n')
    bulletin_result()
    assert seen == [URL]
    assert updates.HEADERS == {"User-Agent": "visa-rag-updates"}


def test_register_updates_are_checked_by_the_real_loader(home, monkeypatch) -> None:
    monkeypatch.setattr(settings, "register_url", "https://example.org/injunctions.toml")
    good = SHIPPED_REGISTER.read_bytes()
    bad = good.replace(b'"8 CFR 214.1"', b'"not a citation"', 1)
    older = good.replace(b"checked = 2026-", b"checked = 2025-")
    for body, want in ((bad, "unreadable provisions"), (older, "checked less recently")):
        serve(monkeypatch, body)
        r = result("injunction register")
        assert r.status == "kept" and want in r.detail
    serve(monkeypatch, good)
    assert result("injunction register").status == "updated"
