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
    # Chosen 2026-09-29 over gemma3:4b, qwen3.5:4b and granite4.1:3b (STATE.md finding
    # 30): fewest unsupported claims in a sentence-level audit, the steadiest across
    # repeats, and the fastest. Not smarter at applying rules — no model was; that is
    # what rules.py is for.
    chat_model: str = "qwen3:4b-instruct"
    # "cited": the answer as JSON with quotes checked word for word against the sources
    # (cited.py). "prose": free text with inline [n]. See STATE.md finding 31.
    answer_format: str = "cited"
    # Reasoning ("thinking") for models that have it, e.g. qwen3.5 — on by default
    # there, and it spent 235 tokens on "150 minus 110". None sends nothing, because a
    # model without thinking may reject the parameter. VISA_THINK=false to turn it off.
    think: bool | None = None
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
    # And at most this many per *section*, which the citation cap cannot see. After
    # enumerated provisions are split, fragments share a citation while sibling
    # subsections carry different ones — so 204.12(c) x3 plus (g) and (d) each stayed
    # inside the citation cap while section 204.12 took 5 of 12 slots on a general NIW
    # question. 204.12 is the waiver for *physicians*: the model got five chunks of the
    # wrong standard against one carrying Dhanasar, and answered from the wrong one.
    # Kept above per_citation_cap so a split provision can still cite several criteria.
    per_section_cap: int = 4
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
    # Issue planning emits three to six short query lines. Nothing capped generation, so
    # a small model stuck in a greedy repetition loop ran until the 16k context filled.
    plan_max_tokens: int = 256
    # Calibrated, not guessed: across 7 in-domain and 5 off-domain probes the lowest
    # genuine query scored 0.688 and the highest irrelevant one 0.544. Midpoint.
    # Re-measure if `embed_model` changes; the floor is model-specific.
    min_score: float = 0.62
    # After answering, ask which source supports each claim that cites none, accepting
    # "nothing does" as an answer. Costs one appended turn — ~10% of answer time, since
    # the sources are already in the KV cache. Off makes answers faster and quieter
    # about what they cannot support.
    attribute_claims: bool = True
    chunk_tokens: int = 700
    # Policy Manual sections are packed tighter. Its concise statement of a test often
    # closes a long section — the Dhanasar three-prong summary is the last paragraph of a
    # 403-word section — and models below 3B read the opening of a chunk and stop. Dense X
    # Retrieval found the largest gain for retrieval units of 100-200 words.
    pm_chunk_tokens: int = 330
    chunk_overlap: int = 80

    # How long a register entry may go unchecked before it is called stale. Shorter
    # than the corpus cadence on purpose: an injunction can be lifted or made permanent
    # between two editions of the CFR, and a false warning is as misleading as none.
    register_refresh_days: int = 14
    # Where to pull a newer register from. Empty means use the copy shipped with the
    # code — an unreachable URL must degrade to that floor, never to silence.
    register_url: str = (
        "https://raw.githubusercontent.com/nitSubedi/visa-rag/main/register/injunctions.toml"
    )

    # Where to pull a newer Visa Bulletin table from, same contract as register_url.
    # Rebuilt daily by .github/workflows/bulletin.yml from agreeing republications.
    bulletin_url: str = (
        "https://raw.githubusercontent.com/nitSubedi/visa-rag/main/bulletin/visa_bulletin.toml"
    )
    # How often to look for a newer register or bulletin. The bulletin changes monthly
    # and an injunction can change any day; a day bounds the lag without a request on
    # every question.
    updates_every_hours: int = 24

    @property
    def bulletin(self) -> Path:
        """The monthly Visa Bulletin table. Public; contains nothing about the user."""
        return self.home / "bulletin"

    @property
    def bulletin_defs(self) -> Path:
        """The table shipped with the code. Empty until a maintainer imports a month:
        travel.state.gov refuses scripted clients, so a person saves the page and
        scripts/bulletin_import.py reads it — cutoff dates are never typed by hand."""
        return Path(__file__).resolve().parents[2] / "bulletin"

    @property
    def register(self) -> Path:
        """Litigation status. Public information; contains nothing about the user."""
        return self.home / "register"

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

    @property
    def register_defs(self) -> Path:
        """The litigation register shipped with the code. Not in `sources/`: that
        directory is corpus definitions, and load_defs builds a Source from every file
        in it — putting the register there broke `visa refresh` outright."""
        return Path(__file__).resolve().parents[2] / "register"

    def ensure_dirs(self) -> None:
        for d in (
            self.corpus,
            self.register,
            self.me,
            self.me / "docs",
            self.me / "memory",
        ):
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
