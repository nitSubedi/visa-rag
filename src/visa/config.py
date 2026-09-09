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
    # 7b, not 14b. Measured on this corpus: 13/16 vs 12/16, 185s vs 361s, and 5.5 GB
    # vs 11 GB. On an 18 GB machine the 14b forces ~4.6 GB of the user's running
    # applications out to swap, which is what made the tool unusable alongside other
    # work. The smaller model is better here on every axis measured.
    chat_model: str = "qwen2.5:7b"
    # Ollama holds a model resident for 5 minutes after each request by default, so
    # 5.5 GB stayed pinned long after an answer printed. Release it sooner; reloading
    # from page cache costs a couple of seconds.
    keep_alive: str = "90s"

    top_k: int = 12
    # Share of top_k reserved for the user's own words before spotted issues
    # interleave. Pure round-robin gave the question 1/n of the budget, so the
    # provision that actually governed the asked-about situation lost to issues the
    # person never raised. See Index.search_many.
    primary_share: float = 0.5
    # At most this many chunks per citation, so one sprawling section cannot swallow
    # the slate. Documented as 3 but implemented as 4-then-uncapped, which is how an
    # EB-1A query came to fill 12 slots with 7 distinct chapters.
    per_citation_cap: int = 3
    # 12 chunks of legal text is ~8.3k tokens; at the old 8192 the prompt overflowed
    # and silently dropped whatever came first — which was the computed-deadline
    # block. Anything set here must leave room for the sources plus the answer.
    num_ctx: int = 16384
    answer_reserve_tokens: int = 1400  # headroom kept free for the response
    # Conversation state. Every question used to be answered as if asked by a
    # stranger, so a follow-up could not build on what came before. Kept small and
    # hard-capped: history competes with sources for the same window, and finding 0
    # was caused by exactly this kind of silent overflow.
    history_turns: int = 3
    history_tokens: int = 700
    retrieval: str = "issues"  # issues | single
    # Issue planning is a model call too, and it was the only one never pinned.
    # Generation ran at temperature 0 while `plan_issues` sampled at 0.3, so the
    # issue list — and therefore the entire retrieval slate the answer is written
    # from — was resampled every run: the same suite scored 13/16 and 12/16 an hour
    # apart with no code change. Finding 0b's rule applies to the whole pipeline,
    # not just its last stage, and a research tool that returns different sources
    # for the same question twice is not reproducible for the user either.
    # Raise it only to deliberately sample issue-spotting, and raise `--repeat` with it.
    plan_temperature: float = 0.0
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
