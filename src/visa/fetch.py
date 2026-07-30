"""Fetchers. The only part of the system that touches the network."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from .sources import Source

# uscis.gov rejects unfamiliar agents with 403. These are public documents fetched
# once per refresh cycle for personal use.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

ECFR_TITLES = "https://www.ecfr.gov/api/versioner/v1/titles.json"


def _ssl_context() -> ssl.SSLContext:
    """Python does not use the macOS system trust store, so a stock urlopen fails
    on .gov TLS with CERTIFICATE_VERIFY_FAILED even though curl succeeds."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _curl(url: str, timeout: int = 180) -> bytes:
    """uscis.gov fingerprints the TLS handshake, not the headers — Python's OpenSSL
    signature gets a 403 no matter what User-Agent it sends, while curl succeeds.
    Shelling out is the only reliable way to reach these public documents."""
    out = subprocess.run(
        [
            "curl",
            "-sSL",
            "--fail",
            "--compressed",
            "-A",
            UA,
            "--max-time",
            str(timeout),
            url,
        ],
        capture_output=True,
    )
    if out.returncode != 0:
        raise RuntimeError(
            f"curl failed ({out.returncode}) for {url}: "
            f"{out.stderr.decode(errors='ignore').strip()[:200]}"
        )
    return out.stdout


def _get(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            body: bytes = r.read()
        return body
    except urllib.error.HTTPError as e:
        if e.code in (403, 406, 429) and shutil.which("curl"):
            return _curl(url, timeout)
        raise
    except urllib.error.URLError:
        if shutil.which("curl"):
            return _curl(url, timeout)
        raise


def _noop_msg(_: str) -> None:
    return None


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _today() -> str:
    return dt.date.today().isoformat()


def ecfr_edition(title: str) -> dict[str, object]:
    """Resolve the latest published edition of a CFR title.

    Requesting today's date 404s — eCFR only serves dates it has actually issued.
    `latest_issue_date` is what to ask for; `latest_amended_on` is the legally
    meaningful currency of the text and is what we surface to the user.
    """
    data: dict[str, list[dict[str, object]]] = json.loads(_get(ECFR_TITLES, timeout=60))
    for t in data.get("titles", []):
        if str(t.get("number")) == str(title):
            return t
    raise RuntimeError(f"eCFR has no title {title}")


def fetch(src: Source, progress: Callable[[str], None] = _noop_msg) -> dict[str, object]:
    """Download a source into <shard>/raw/. Returns a manifest dict.

    Every fetched file is hashed so a recipient of a shared bundle can verify the
    text against the .gov original rather than trusting a binary blob.
    """
    raw = src.dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []

    if src.fetcher == "uscode-zip":
        url = src.all_urls[0]
        progress(f"fetching {url}")
        blob = _get(url)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for name in z.namelist():
                if name.lower().endswith(".xml"):
                    data = z.read(name)
                    p = raw / Path(name).name
                    p.write_bytes(data)
                    files.append(
                        {
                            "name": p.name,
                            "url": url,
                            "sha256": sha256(data),
                            "bytes": len(data),
                        }
                    )
    else:
        edition: dict[str, object] = {}
        for url in src.all_urls:
            u = url
            if "{date}" in url:
                m = re.search(r"title-(\w+)\.", url)
                edition = ecfr_edition(m.group(1) if m else "8")
                u = url.replace("{date}", str(edition["latest_issue_date"]))
            progress(f"fetching {u}")
            data = _get(u)
            # Sniff rather than trust the declared fetcher: a guidance source often
            # mixes HTML pages with PDFs, and the chunker dispatches on suffix.
            ext = (
                ".pdf"
                if data[:4] == b"%PDF"
                else {"ecfr-xml": ".xml", "html": ".html", "pdf": ".pdf"}.get(
                    src.fetcher, ".txt"
                )
            )
            stem = Path(u.split("?")[0]).name or src.slug
            if not stem.endswith(ext):
                stem = f"{stem}{ext}"
            p = raw / stem
            p.write_bytes(data)
            files.append(
                {"name": p.name, "url": u, "sha256": sha256(data), "bytes": len(data)}
            )

    manifest: dict[str, object] = {
        "slug": src.slug,
        "title": src.title,
        "tier": src.tier,
        "kind": src.kind,
        "jurisdiction": src.jurisdiction,
        "fetched": _today(),
        "refresh_days": src.refresh_days,
        "files": files,
        "note": src.note,
    }
    if src.fetcher == "ecfr-xml" and edition:
        # The date the law was last amended matters more than when we downloaded it.
        manifest["amended_on"] = edition.get("latest_amended_on")
        manifest["edition"] = edition.get("latest_issue_date")
        manifest["upstream_current_as_of"] = edition.get("up_to_date_as_of")
    (src.dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def age_days(manifest: dict[str, object]) -> int:
    try:
        return (dt.date.today() - dt.date.fromisoformat(str(manifest["fetched"]))).days
    except (KeyError, ValueError):
        return 9999


def is_stale(manifest: dict[str, object]) -> bool:
    return age_days(manifest) > int(str(manifest.get("refresh_days", 30)))
