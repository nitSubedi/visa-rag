"""visa — local, offline, citation-gated immigration research."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import answer as ans
from . import chunk, dates, embed, fetch, profile, store
from .config import settings, tier_label
from .models import Chunk, Manifest
from .search import Hit, Index
from .sources import Source, load_def, load_defs

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Local, offline immigration research over primary sources.",
)
c = Console()

PRIVATE = Source(
    slug="me",
    title="My documents",
    tier=9,
    fetcher="local",
    chunker="text",
    kind="personal",
    private=True,
)


# --------------------------------------------------------------------- helpers


def _index_source(src: Source, refetch: bool = True) -> int:
    if refetch and src.all_urls:
        fetch.fetch(src, progress=lambda m: c.print(f"  [dim]{m}[/dim]"))
    rows = chunk.chunk_source(src)
    if not rows:
        c.print(f"  [yellow]no chunks produced for {src.slug}[/yellow]")
        return 0
    with c.status(f"embedding {len(rows):,} chunks…"):
        vecs = embed.embed([r.embed_text or r.text for r in rows])
    extra: dict[str, object] = {"private": src.private} if src.private else {}
    if not (src.dir / "manifest.json").exists():
        (src.dir).mkdir(parents=True, exist_ok=True)
        (src.dir / "manifest.json").write_text(
            json.dumps(
                {
                    "slug": src.slug,
                    "title": src.title,
                    "tier": src.tier,
                    "kind": src.kind,
                    "fetched": ans.today(),
                    "refresh_days": src.refresh_days,
                    "note": src.note,
                    "files": [],
                },
                indent=2,
            )
        )
    store.write_shard(src.dir, rows, vecs, extra)
    c.print(f"  [green]✓[/green] {src.slug}: {len(rows):,} chunks")
    return len(rows)


def _load_index() -> Index:
    idx = Index.load()
    if not idx.rows:
        c.print("[yellow]No corpus indexed yet. Run:[/yellow] visa init")
        raise typer.Exit(1)
    return idx


# -------------------------------------------------------------------- commands


@app.command()
def init(only: str = typer.Option(None, help="Only this source slug")) -> None:
    """Download the corpus and build the index. The only step that needs network."""
    settings.ensure_dirs()
    if profile.init():
        c.print(f"[green]created[/green] {settings.profile_path}")
    defs = [s for s in load_defs() if not only or s.slug == only]
    total = 0
    failed: list[tuple[str, str]] = []
    for s in defs:
        c.print(f"[bold]{s.slug}[/bold] — {s.title} [dim](tier {s.tier})[/dim]")
        try:
            total += _index_source(s)
        except Exception as e:
            failed.append((s.slug, str(e)))
            c.print(f"  [red]failed:[/red] {e}")
    if failed:
        c.print(
            f"\n[bold red]{len(failed)} source(s) failed[/bold red] — "
            f"the corpus is incomplete, so answers will have gaps:"
        )
        for slug, err in failed:
            c.print(f"  [red]·[/red] {slug}: {err[:110]}")
        c.print(
            "[yellow]Fix the errors and re-run `visa init` before relying on it.[/yellow]"
        )
    if total:
        c.print(
            f"\n[bold green]{total:,} chunks indexed.[/bold green]"
            + ("" if failed else " Fully offline from here. Try: [bold]visa ask[/bold]")
        )
    else:
        c.print("\n[red]Nothing indexed.[/red]")
        raise typer.Exit(1)


@app.command()
def index(
    slug: str = typer.Argument(..., help="Source slug, or 'me' for your docs"),
) -> None:
    """Re-chunk and re-embed one shard without re-downloading."""
    src: Source | None = PRIVATE if slug == "me" else load_def(slug)
    if src is None:
        c.print(f"[red]unknown source:[/red] {slug}")
        raise typer.Exit(1)
    _index_source(src, refetch=False)


@app.command()
def refresh(force: bool = typer.Option(False, "--force")) -> None:
    """Re-download sources whose cached copy is older than their refresh cadence."""
    for s in load_defs():
        mf = s.dir / "manifest.json"
        man = Manifest.model_validate_json(mf.read_text()) if mf.exists() else None
        if force or man is None or man.is_stale:
            age = man.age_days if man else 0
            c.print(f"[bold]{s.slug}[/bold] (age {age}d)")
            _index_source(s)
        else:
            c.print(f"[dim]{s.slug}: fresh ({man.age_days}d)[/dim]")


@app.command()
def add(
    path: str = typer.Argument(None, help="File or folder to add to YOUR documents"),
    source: str = typer.Option(None, "--source", help="Path to a corpus .toml"),
) -> None:
    """Add a personal document, or register a whole new corpus."""
    if source:
        p = Path(source).expanduser()
        dest = settings.source_defs / p.name
        shutil.copy(p, dest)
        s = load_def(p.stem)
        if s is None:
            dest.unlink(missing_ok=True)
            c.print(f"[red]could not load {p.name}[/red] — check the TOML fields")
            raise typer.Exit(1)
        c.print(f"[green]registered[/green] {p.stem}")
        _index_source(s)
        return
    if not path:
        c.print("[red]give a file/folder, or --source a .toml[/red]")
        raise typer.Exit(1)
    settings.ensure_dirs()
    src_p = Path(path).expanduser()
    docs = settings.me / "docs"
    files = [src_p] if src_p.is_file() else sorted(src_p.rglob("*"))
    n = 0
    for f in files:
        if f.is_file() and f.suffix.lower() in (".pdf", ".txt", ".md", ".html", ".xml"):
            shutil.copy(f, docs / f.name)
            n += 1
    c.print(f"copied {n} file(s) → [dim]{docs}[/dim] (local only)")
    PRIVATE.dir.mkdir(parents=True, exist_ok=True)
    _reindex_private()


def _reindex_private() -> None:
    docs = settings.me / "docs"
    rows: list[Chunk] = []
    if docs.exists():
        rows = chunk.chunk_text(sorted(p for p in docs.iterdir() if p.is_file()), PRIVATE)
    mem = settings.me / "memory"
    if mem.exists():
        rows += chunk.chunk_text(sorted(p for p in mem.iterdir() if p.is_file()), PRIVATE)
    for i, r in enumerate(rows):
        r.id = f"me:{i}"
    if not rows:
        c.print("[dim]no personal documents yet[/dim]")
        return
    with c.status(f"embedding {len(rows)} personal chunks…"):
        vecs = embed.embed([r.embed_text or r.text for r in rows])
    PRIVATE.dir.mkdir(parents=True, exist_ok=True)
    (PRIVATE.dir / "manifest.json").write_text(
        json.dumps(
            {
                "slug": "me",
                "title": "My documents",
                "tier": 9,
                "kind": "personal",
                "fetched": ans.today(),
                "refresh_days": 3650,
                "private": True,
                "files": [],
            },
            indent=2,
        )
    )
    store.write_shard(PRIVATE.dir, rows, vecs, {"private": True})
    c.print(f"  [green]✓[/green] me: {len(rows)} chunks [dim](never shared)[/dim]")


@app.command()
def remember(
    text: str = typer.Argument(..., help="Something to remember about you"),
) -> None:
    """Record a freeform fact; it becomes retrievable context."""
    settings.ensure_dirs()
    p = settings.me / "memory" / "notes.md"
    prev = p.read_text() if p.exists() else ""
    p.write_text(prev + f"\n- ({ans.today()}) {text}\n")
    c.print("[green]noted.[/green]")
    _reindex_private()


@app.command()
def sources() -> None:
    """What's indexed, at what precedence, and how fresh."""
    t = Table(show_header=True, header_style="bold")
    for col in ("shard", "tier", "chunks", "fetched", "age", "status"):
        t.add_column(col)
    for s in store.load_shards():
        m = s.manifest
        age = m.age_days
        stale = m.is_stale
        t.add_row(
            s.slug,
            f"{m.tier} {tier_label(m.tier)}",
            f"{m.chunks:,}",
            m.fetched.isoformat(),
            f"{age}d",
            "[red]stale[/red]" if stale else "[green]fresh[/green]",
        )
    c.print(t)
    c.print(
        "[dim]Priority dates are intentionally not cached — check travel.state.gov.[/dim]"
    )


@app.command("profile")
def profile_cmd(
    key: str = typer.Argument(None), value: str = typer.Argument(None)
) -> None:
    """Show your profile, or set a field: visa profile program_end_date 2027-05-15"""
    if key and value:
        profile.set_value(key, value)
        c.print(f"[green]set[/green] {key} = {value}")
    d = profile.load()
    if not d:
        profile.init()
        c.print(
            f"created {settings.profile_path} — edit it, "
            "or use: visa profile <key> <value>"
        )
        return
    c.print(
        Panel(
            profile.render(d) or "(empty)",
            title=str(settings.profile_path),
            border_style="dim",
        )
    )
    dr = dates.render(d)
    if dr:
        c.print(Panel(dr, title="computed deadlines", border_style="cyan"))


def _render_hits(hits: list[Hit], full: int | None = None) -> None:
    if full:
        h = hits[full - 1]
        c.print(
            Panel(
                h.row.text,
                title=f"[{full}] {h.row.citation}",
                subtitle=h.row.path,
                border_style="cyan",
            )
        )
        return
    t = Table(show_header=False, box=None, padding=(0, 1))
    for i, cite, tier, score in ans.sources_table(hits):
        t.add_row(
            f"[dim]\\[{i}][/dim]", cite, f"[dim]{tier}[/dim]", f"[dim]{score}[/dim]"
        )
    c.print(t)


@app.command()
def ask(
    question: str | None = typer.Argument(None),
    k: int | None = typer.Option(None, "-k"),
) -> None:
    """Ask a question. With no argument, opens a REPL."""
    idx = _load_index()
    if question:
        _answer_once(idx, question, k)
        return
    _repl(idx, k)


def _show_issues(issues: list[str]) -> None:
    c.print("[dim]issues identified:[/dim]")
    for i in issues:
        c.print(f"  [dim]· {i}[/dim]")


def _answer_once(
    idx: Index,
    q: str,
    k: int | None = None,
    turns: list[ans.Turn] | None = None,
) -> tuple[list[Hit], str]:
    with c.status("[dim]retrieving…[/dim]"):
        hits, gated, warnings = ans.answer(q, idx, k, on_issue=_show_issues, turns=turns)
    for w in warnings:
        c.print(f"[yellow]⚠ {w}[/yellow]")
    if not gated:
        c.print(
            "[yellow]The sources I have don't cover this well enough to answer.[/yellow]"
        )
        if hits:
            c.print("[dim]Closest passages, for what they're worth:[/dim]")
            _render_hits(hits[:4])
        return [], ""
    msgs = ans.build_prompt(q, hits, turns=turns)
    c.print()
    buf: list[str] = []
    first = True
    status = c.status("[dim]reading the sources…[/dim]")
    status.start()
    try:
        for tok in ans.stream_chat(msgs):
            if first:
                status.stop()
                first = False
            buf.append(tok)
            c.print(tok, end="", markup=False, highlight=False)
    except Exception as e:
        status.stop()
        c.print(f"\n[red]generation failed:[/red] {e}")
        return [], ""
    finally:
        if first:
            status.stop()
    c.print("\n")
    text = "".join(buf)
    # Show the computed deadlines rather than trusting the model to repeat them.
    # verify_dates catches a date the model got *wrong*; nothing caught a date it
    # simply omitted, and omission is the common failure — three of the eval's date
    # checks fail that way. These are Python's arithmetic, so print them directly.
    if deadlines := dates.render(profile.load()):
        c.print(
            Panel(
                deadlines,
                title="[cyan]computed deadlines[/cyan]",
                subtitle="[dim]calculated from your profile, not model output[/dim]",
                border_style="cyan",
            )
        )
    problems = (
        ans.verify_citations(text, hits)
        + ans.verify_dates(text, hits=hits)
        + ans.verify_dialogue(text)
        + ans.verify_grounding(text)
    )
    if problems:
        c.print(
            Panel(
                "\n".join(f"· {p}" for p in problems),
                title="[red]answer check failed[/red]",
                subtitle="[red]verify these against the sources first[/red]",
                border_style="red",
            )
        )
    c.rule("[dim]sources[/dim]", style="dim")
    _render_hits(hits)
    c.print(f"[dim]{ans.CLOSER}[/dim]")
    return hits, text


def _repl(idx: Index, k: int | None) -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    settings.ensure_dirs()
    sess: PromptSession[str] = PromptSession(
        history=FileHistory(str(settings.me / ".history"))
    )
    shards = ", ".join(f"{s.slug}({len(s):,})" for s in idx.shards)
    c.print(
        Panel(
            f"[bold]visa[/bold] — local · {settings.chat_model} · "
            f"{len(idx.rows):,} chunks\n"
            f"[dim]{shards}[/dim]\n"
            f"[dim]/sources N · /profile · /refresh · /help · /quit[/dim]",
            border_style="cyan",
        )
    )
    last: list[Hit] = []
    turns: list[ans.Turn] = []
    while True:
        try:
            q = sess.prompt("\nask> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q in ("/quit", "/exit", "/q"):
            break
        if q == "/help":
            c.print(
                "[dim]/sources N — print passage N verbatim\n/profile — your facts\n"
                "/refresh — re-pull stale corpora\n/quit[/dim]"
            )
            continue
        if q.startswith("/sources"):
            parts = q.split()
            if len(parts) > 1 and parts[1].isdigit() and last:
                n = int(parts[1])
                if 1 <= n <= len(last):
                    _render_hits(last, full=n)
                    continue
            sources()
            continue
        if q == "/profile":
            profile_cmd()
            continue
        if q == "/refresh":
            refresh()
            idx = Index.load()
            continue
        hits, text = _answer_once(idx, q, k, turns)
        last = hits or last
        if text:
            turns.append(ans.Turn(question=q, answer=text))


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        ask(None, None)


if __name__ == "__main__":
    app()
