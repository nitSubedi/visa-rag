"""Paths and settings. Everything personal lives under HOME_DIR/me and never leaves."""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path(os.environ.get("VISA_HOME", Path.home() / ".visa"))
CORPUS = HOME / "corpus"          # public law — shareable
ME = HOME / "me"                  # profile, docs, memory — never shared
PROFILE = ME / "profile.toml"

REPO = Path(__file__).resolve().parents[2]
SOURCE_DEFS = REPO / "sources"

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.environ.get("VISA_EMBED_MODEL", "nomic-embed-text")
CHAT_MODEL = os.environ.get("VISA_CHAT_MODEL", "qwen2.5:14b")

# Retrieval
TOP_K = 12                 # chunks handed to the model
# Calibrated, not guessed: across 7 in-domain and 5 off-domain probes the lowest
# genuine query scored 0.688 and the highest irrelevant one 0.544. Midpoint.
MIN_SCORE = 0.62           # below this, the citation gate refuses to answer
CHUNK_TOKENS = 700
CHUNK_OVERLAP = 80

# Tier -> (label, weight). Lower tier number = higher legal precedence.
TIERS = {
    1: ("statute", 1.00),
    2: ("regulation", 0.97),
    3: ("policy", 0.92),
    4: ("guidance", 0.88),
    9: ("personal", 1.00),
}


def tier_label(t: int) -> str:
    return TIERS.get(t, ("unknown", 0.8))[0]


def tier_weight(t: int) -> float:
    return TIERS.get(t, ("unknown", 0.8))[1]


def ensure_dirs() -> None:
    for d in (CORPUS, ME, ME / "docs", ME / "memory"):
        d.mkdir(parents=True, exist_ok=True)
    # Personal data must never be committed, even if the user inits a repo here.
    gi = ME / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n")
