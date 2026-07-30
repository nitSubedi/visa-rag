"""The user's structured facts. Local only, never included in a shared bundle."""

from __future__ import annotations

import datetime as dt
import tomllib

from .config import settings

TEMPLATE = """# Your facts. Local only — never shared.
# Leave anything blank you'd rather not record. Dates are ISO: YYYY-MM-DD

status              = "F-1"       # F-1, J-1, H-1B, O-1, ...
country_of_birth    = ""          # drives EB priority-date backlogs
degree_level        = ""          # bachelors | masters | phd
field               = ""          # e.g. computer science
school              = ""
program_end_date    = ""          # I-20 program end date
opt_start_date      = ""
opt_end_date        = ""
stem_extension      = false
employer            = ""
i94_expiry          = ""
prior_filings       = ""          # e.g. "I-765 filed 2026-03-02"
goals               = ""          # e.g. "EB-1A / NIW as a startup founder"
notes               = ""
"""

FIELDS = [
    line.split("=")[0].strip()
    for line in TEMPLATE.splitlines()
    if "=" in line and not line.strip().startswith("#")
]


def load() -> dict[str, object]:
    if not settings.profile_path.exists():
        return {}
    try:
        return tomllib.loads(settings.profile_path.read_text())
    except Exception:
        return {}


def init() -> bool:
    """Create the template if absent. Returns True if newly created."""
    settings.ensure_dirs()
    if settings.profile_path.exists():
        return False
    settings.profile_path.write_text(TEMPLATE)
    return True


def _fmt(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dt.date):
        return f'"{v.isoformat()}"'
    return f'"{v}"'


def set_value(key: str, value: str) -> None:
    data = load()
    if value.lower() in ("true", "false"):
        data[key] = value.lower() == "true"
    else:
        data[key] = value
    lines = ["# Your facts. Local only — never shared.", ""]
    for k in FIELDS:
        if k in data:
            lines.append(f"{k:<19} = {_fmt(data[k])}")
    for k, v in data.items():
        if k not in FIELDS:
            lines.append(f"{k:<19} = {_fmt(v)}")
    settings.profile_path.write_text("\n".join(lines) + "\n")


def render(data: dict[str, object] | None = None) -> str:
    d = data if data is not None else load()
    kept = {k: v for k, v in d.items() if v not in ("", None, False)}
    if not kept:
        return ""
    body = "\n".join(f"  {k}: {v}" for k, v in kept.items())
    return f"USER PROFILE (their own stated facts, not verified):\n{body}"
