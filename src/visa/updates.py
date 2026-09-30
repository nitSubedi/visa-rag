"""Pull newer public legal data — the injunction register and the Visa Bulletin table.

Both ship with the code, and the shipped copy is the floor: an unreachable URL, a bad
download or no URL at all degrades to it, never to nothing. What is fetched is public
data about the law; the request carries nothing about the user (no query string, no
profile, a generic User-Agent) — updates about the law come in, nothing about the person
goes out.

A download replaces the local copy only if it
  * parses and passes the same checks the loaders rely on,
  * is not older than what is already here — no rolling back to an earlier bulletin
    month or a register checked less recently, whether by mistake or by a bad mirror,
and it is written atomically, so an interrupted download cannot leave half a file.
"""

from __future__ import annotations

import datetime as dt
import re
import tempfile
import time
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import settings

TIMEOUT = 10  # seconds; offline must not hold up an answer
HEADERS = {"User-Agent": "visa-rag-updates"}  # deliberately nothing about the user

_CELL = re.compile(r"C|U|\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Result:
    name: str
    status: str  # updated | unchanged | skipped | kept
    detail: str


def _check_bulletin(new: bytes, old: Path | None) -> str:
    """Why a downloaded bulletin table is not acceptable, or "" if it is."""
    d = tomllib.loads(new.decode())
    month = str(d.get("month", ""))
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return "no bulletin month"
    charts = [d.get(c) for c in ("final_action", "dates_for_filing")]
    if not any(isinstance(t, dict) and t for t in charts):
        return "no chart"  # one chart alone is fine: the other shows as UNCONFIRMED
    for chart in ("final_action", "dates_for_filing"):
        table = d.get(chart) or {}
        if not isinstance(table, dict):
            return f"malformed {chart} chart"
        for cat, row in table.items():
            for area, v in row.items():
                if not _CELL.fullmatch(str(v)):
                    return f"unreadable cell {chart}/{cat}/{area}: {v!r}"
    if d.get("method", "").startswith("consensus") and len(d.get("sources", [])) < 2:
        return "a consensus table must name at least two sources"
    if old is not None and old.exists():
        prev = str(tomllib.loads(old.read_text()).get("month", ""))
        if prev and month < prev:
            return f"older than the local table ({month} < {prev})"
    return ""


def _check_register(new: bytes, old: Path | None) -> str:
    from . import register

    with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as f:
        f.write(new)
    try:
        entries = register.load(Path(f.name))
    finally:
        Path(f.name).unlink()
    if not entries:
        return "no entries"
    for e in entries:
        if not e.provisions or not all(register._parse(p) for p in e.provisions):
            return f"unreadable provisions in {e.case!r}"
    if old is not None and old.exists():
        prev = register.load(old)
        if prev and max(e.checked for e in entries) < max(e.checked for e in prev):
            return "checked less recently than the local register"
    return ""


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return bytes(r.read())


def refresh_one(
    name: str,
    url: str,
    dest: Path,
    shipped: Path,
    check: Callable[[bytes, Path | None], str],
    force: bool = False,
) -> Result:
    if not url:
        return Result(name, "skipped", "no update URL configured; using the shipped copy")
    age_h = (time.time() - dest.stat().st_mtime) / 3600 if dest.exists() else None
    if not force and age_h is not None and age_h < settings.updates_every_hours:
        return Result(name, "unchanged", f"checked {age_h:.0f}h ago")
    try:
        body = _fetch(url)
        why = check(body, dest if dest.exists() else shipped)
    except Exception as e:  # offline, timeout, bad TOML: keep what we have
        return Result(
            name, "kept", f"update failed ({type(e).__name__}); kept current copy"
        )
    if why:
        return Result(name, "kept", f"rejected download: {why}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() == body:
        dest.touch()
        return Result(name, "unchanged", "already up to date")
    with tempfile.NamedTemporaryFile("wb", dir=dest.parent, delete=False) as f:
        f.write(body)
    Path(f.name).replace(dest)  # atomic
    return Result(name, "updated", f"downloaded {dt.date.today().isoformat()}")


def refresh_all(force: bool = False) -> list[Result]:
    from . import register

    out = [
        refresh_one(
            "injunction register",
            settings.register_url,
            settings.register / "injunctions.toml",
            settings.register_defs / "injunctions.toml",
            _check_register,
            force,
        ),
        refresh_one(
            "Visa Bulletin",
            settings.bulletin_url,
            settings.bulletin / "visa_bulletin.toml",
            settings.bulletin_defs / "visa_bulletin.toml",
            _check_bulletin,
            force,
        ),
    ]
    if any(r.status == "updated" for r in out):
        register.clear_cache()
    return out
