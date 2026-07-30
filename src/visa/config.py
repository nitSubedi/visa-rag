"""Typed settings. Twelve-Factor config via environment, with `VISA_` prefix."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Tier -> (label, retrieval weight). Lower number = higher legal precedence.
TIERS: dict[int, tuple[str, float]] = {
    1: ("statute", 1.00),
    2: ("regulation", 0.97),
    3: ("policy", 0.92),
    4: ("guidance", 0.88),
    9: ("personal", 1.00),
}


class Settings(BaseSettings):
    """Runtime configuration. Override any field with `VISA_<FIELD>` or a `.env`."""

    model_config = SettingsConfigDict(env_prefix="VISA_", env_file=".env", extra="ignore")

    home: Path = Field(default_factory=lambda: Path.home() / ".visa")
    ollama_host: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"
    chat_model: str = "qwen2.5:14b"

    top_k: int = 12
    # 12 chunks of legal text is ~8.3k tokens; at the old 8192 the prompt overflowed
    # and silently dropped whatever came first — which was the computed-deadline
    # block. Anything set here must leave room for the sources plus the answer.
    num_ctx: int = 16384
    answer_reserve_tokens: int = 1400  # headroom kept free for the response
    retrieval: str = "issues"  # issues | single
    # Calibrated, not guessed: across 7 in-domain and 5 off-domain probes the lowest
    # genuine query scored 0.688 and the highest irrelevant one 0.544. Midpoint.
    # Re-measure if `embed_model` changes; the floor is model-specific.
    min_score: float = 0.62
    chunk_tokens: int = 700
    chunk_overlap: int = 80

    @property
    def corpus(self) -> Path:
        """Public law. Shareable."""
        return self.home / "corpus"

    @property
    def me(self) -> Path:
        """Profile, documents, memory. Never shared."""
        return self.home / "me"

    @property
    def profile_path(self) -> Path:
        return self.me / "profile.toml"

    @property
    def source_defs(self) -> Path:
        return Path(__file__).resolve().parents[2] / "sources"

    def ensure_dirs(self) -> None:
        for d in (self.corpus, self.me, self.me / "docs", self.me / "memory"):
            d.mkdir(parents=True, exist_ok=True)
        # Personal data must never be committed, even if a repo is created here.
        gitignore = self.me / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n")


settings = Settings()


def tier_label(tier: int) -> str:
    return TIERS.get(tier, ("unknown", 0.8))[0]


def tier_weight(tier: int) -> float:
    return TIERS.get(tier, ("unknown", 0.8))[1]
