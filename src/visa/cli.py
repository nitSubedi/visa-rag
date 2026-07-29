"""visa — local, offline, citation-gated immigration research."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import answer as ans
from . import chunk, config, dates, embed, fetch, profile, store
from .search import Index
from .sources import Source, load_def, load_defs

app = typer.Typer(add_completion=False, no_args_is_help=False,
                  help="Local, offline immigration research over primary sources.")
c = Console()

PRIVATE = Source(slug="me", title="My documents", tier=9, fetcher="local",
                 chunker="text", kind="personal", private=True)


# --------------------------------------------------------------------- helpers

def _index_source(src: Source, refetch: bool = True) -> int:
    if refetch and src.all_urls:
        fetch.fetch(src, progress=lambda m: c.print(f"  [dim]{m}[/dim]"))
    rows = chunk.chunk_source(src)
    if not rows:
        c.print(f"  [yellow]no chunks produced for {src.slug}[/yellow]")
        return 0
    with c.status(f"embedding {len(rows):,} chunks…"):
        vecs = embed.embed([r["text"] for r in rows])
    extra = {"private": src.private} if src.private else {}
    if not (src.dir / "manifest.json").exists():
        (src.dir).mkdir(parents=True, exist_ok=True)
        (src.dir / "manifest.json").write_text(json.dumps({
            "slug": src.slug, "title": src.title, "tier": src.tier,
            "kind": src.kind, "fetched": ans.today(),
            "refresh_days": src.refresh_days, "note": src.note, "files": [],
        }, indent=2))
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
def init(only: str = typer.Option(None, help="Only this source slug")):
    """Download the corpus and build the index. The only step that needs network."""
    config.ensure_dirs()
    if profile.init():
        c.print(f"[green]created[/green] {config.PROFILE}")
    defs = [s for s in load_defs() if not only or s.slug == only]
    total, failed = 0, []
    for s in defs:
        c.print(f"[bold]{s.slug}[/bold] — {s.title} [dim](tier {s.tier})[/dim]")
        try:
            total += _index_source(s)
        except Exception as e:
            failed.append((s.slug, str(e)))
            c.print(f"  [red]failed:[/red] {e}")
    if failed:
        c.print(f"\n[bold red]{len(failed)} source(s) failed[/bold red] — "
                f"the corpus is incomplete, so answers will have gaps:")
        for slug, err in failed:
            c.print(f"  [red]·[/red] {slug}: {err[:110]}")
        c.print("[yellow]Fix the errors and re-run `visa init` before relying on it.[/yellow]")
    if total:
        c.print(f"\n[bold green]{total:,} chunks indexed.[/bold green]"
                + ("" if failed else " Fully offline from here. Try: [bold]visa ask[/bold]"))
    else:
        c.print("\n[red]Nothing indexed.[/red]")
        raise typer.Exit(1)


@app.command()
def index(slug: str = typer.Argument(..., help="Source slug, or 'me' for your docs")):
    """Re-chunk and re-embed one shard without re-downloading."""
    src = PRIVATE if slug == "me" else load_def(slug)
    if not src:
        c.print(f"[red]unknown source:[/red] {slug}")
        raise typer.Exit(1)
    _index_source(src, refetch=False)


@app.command()
def refresh(force: bool = typer.Option(False, "--force")):
    """Re-download sources whose cached copy is older than their refresh cadence."""
    for s in load_defs():
        mf = s.dir / "manifest.json"
        man = json.loads(mf.read_text()) if mf.exists() else {}
        if force or not man or fetch.is_stale(man):
            c.print(f"[bold]{s.slug}[/bold] (age {fetch.age_days(man)}d)")
            _index_source(s)
        else:
            c.print(f"[dim]{s.slug}: fresh ({fetch.age_days(man)}d)[/dim]")


@app.command()
def add(path: str = typer.Argument(None, help="File or folder to add to YOUR documents"),
        source: str = typer.Option(None, "--source", help="Path to a corpus .toml")):
    """Add a personal document, or register a whole new corpus."""
    if source:
        p = Path(source).expanduser()
        dest = config.SOURCE_DEFS / p.name
        shutil.copy(p, dest)
        s = load_def(p.stem)
        c.print(f"[green]registered[/green] {p.stem}")
        _index_source(s)
        return
    if not path:
        c.print("[red]give a file/folder, or --source a .toml[/red]")
        raise typer.Exit(1)
    config.ensure_dirs()
    src_p = Path(path).expanduser()
    docs = config.ME / "docs"
    files = [src_p] if src_p.is_file() else sorted(src_p.rglob("*"))
    n = 0
    for f in files:
        if f.is_file() and f.suffix.lower() in (".pdf", ".txt", ".md", ".html", ".xml"):
            shutil.copy(f, docs / f.name)
            n += 1
    c.print(f"copied {n} file(s) → [dim]{docs}[/dim] (local only)")
    PRIVATE.dir.mkdir(parents=True, exist_ok=True)
    _reindex_private()


def _reindex_private():
    docs = config.ME / "docs"
    rows = []
    if docs.exists():
        rows = chunk.chunk_text(sorted(p for p in docs.iterdir() if p.is_file()), PRIVATE)
    mem = config.ME / "memory"
    if mem.exists():
        rows += chunk.chunk_text(sorted(p for p in mem.iterdir() if p.is_file()), PRIVATE)
    for i, r in enumerate(rows):
        r["id"] = f"me:{i}"
    if not rows:
        c.print("[dim]no personal documents yet[/dim]")
        return
    with c.status(f"embedding {len(rows)} personal chunks…"):
        vecs = embed.embed([r["text"] for r in rows])
    PRIVATE.dir.mkdir(parents=True, exist_ok=True)
    (PRIVATE.dir / "manifest.json").write_text(json.dumps({
        "slug": "me", "title": "My documents", "tier": 9, "kind": "personal",
        "fetched": ans.today(), "refresh_days": 3650, "private": True, "files": [],
    }, indent=2))
    store.write_shard(PRIVATE.dir, rows, vecs, {"private": True})
    c.print(f"  [green]✓[/green] me: {len(rows)} chunks [dim](never shared)[/dim]")


@app.command()
def remember(text: str = typer.Argument(..., help="Something to remember about you")):
    """Record a freeform fact; it becomes retrievable context."""
    config.ensure_dirs()
    p = config.ME / "memory" / "notes.md"
    prev = p.read_text() if p.exists() else ""
    p.write_text(prev + f"\n- ({ans.today()}) {text}\n")
    c.print("[green]noted.[/green]")
    _reindex_private()


@app.command()
def sources():
    """What's indexed, at what precedence, and how fresh."""
    t = Table(show_header=True, header_style="bold")
    for col in ("shard", "tier", "chunks", "fetched", "age", "status"):
        t.add_column(col)
    for s in store.load_shards():
        m = s.manifest
        age = fetch.age_days(m)
        stale = fetch.is_stale(m)
        t.add_row(
            s.slug,
            f"{m.get('tier')} {config.tier_label(int(m.get('tier') or 9))}",
            f"{m.get('chunks', 0):,}",
            str(m.get("fetched", "—")),
            f"{age}d",
            "[red]stale[/red]" if stale else "[green]fresh[/green]",
        )
    c.print(t)
    c.print("[dim]Priority dates are intentionally not cached — check travel.state.gov.[/dim]")


@app.command("profile")
def profile_cmd(key: str = typer.Argument(None), value: str = typer.Argument(None)):
    """Show your profile, or set a field: visa profile program_end_date 2027-05-15"""
    if key and value:
        profile.set_value(key, value)
        c.print(f"[green]set[/green] {key} = {value}")
    d = profile.load()
    if not d:
        profile.init()
        c.print(f"created {config.PROFILE} — edit it, or use: visa profile <key> <value>")
        return
    c.print(Panel(profile.render(d) or "(empty)", title=str(config.PROFILE),
                  border_style="dim"))
    dr = dates.render(d)
    if dr:
        c.print(Panel(dr, title="computed deadlines", border_style="cyan"))


def _render_hits(hits, full: int | None = None):
    if full:
        h = hits[full - 1]
        c.print(Panel(h.row["text"], title=f"[{full}] {h.row['citation']}",
                      subtitle=h.row.get("path", ""), border_style="cyan"))
        return
    t = Table(show_header=False, box=None, padding=(0, 1))
    for i, cite, tier, score in ans.sources_table(hits):
        t.add_row(f"[dim]\\[{i}][/dim]", cite, f"[dim]{tier}[/dim]", f"[dim]{score}[/dim]")
    c.print(t)


@app.command()
def ask(question: str = typer.Argument(None), k: int = typer.Option(None, "-k")):
    """Ask a question. With no argument, opens a REPL."""
    idx = _load_index()
    if question:
        _answer_once(idx, question, k)
        return
    _repl(idx, k)


def _answer_once(idx: Index, q: str, k: int | None = None):
    hits, gated, warnings = ans.answer(q, idx, k)
    for w in warnings:
        c.print(f"[yellow]⚠ {w}[/yellow]")
    if not gated:
        c.print("[yellow]The sources I have don't cover this well enough to answer.[/yellow]")
        if hits:
            c.print("[dim]Closest passages, for what they're worth:[/dim]")
            _render_hits(hits[:4])
        return
    msgs = ans.build_prompt(q, hits)
    c.print()
    buf = []
    try:
        for tok in ans.stream_chat(msgs):
            buf.append(tok)
            c.print(tok, end="", markup=False, highlight=False)
    except Exception as e:
        c.print(f"\n[red]generation failed:[/red] {e}")
        return
    c.print("\n")
    problems = ans.verify_citations("".join(buf), hits)
    if problems:
        c.print(Panel(
            "\n".join(f"· {p}" for p in problems),
            title="[red]citation check failed[/red]",
            subtitle="[red]verify these against the sources before relying on them[/red]",
            border_style="red"))
    c.rule("[dim]sources[/dim]", style="dim")
    _render_hits(hits)
    c.print(f"[dim]{ans.CLOSER}[/dim]")
    return hits


def _repl(idx: Index, k: int | None):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    config.ensure_dirs()
    sess = PromptSession(history=FileHistory(str(config.ME / ".history")))
    shards = ", ".join(f"{s.slug}({len(s):,})" for s in idx.shards)
    c.print(Panel(
        f"[bold]visa[/bold] — local · {config.CHAT_MODEL} · {len(idx.rows):,} chunks\n"
        f"[dim]{shards}[/dim]\n"
        f"[dim]/sources N · /profile · /refresh · /help · /quit[/dim]",
        border_style="cyan"))
    last = []
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
            c.print("[dim]/sources N — print passage N verbatim\n/profile — your facts\n"
                    "/refresh — re-pull stale corpora\n/quit[/dim]")
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
        last = _answer_once(idx, q, k) or last


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        ask(None, None)


if __name__ == "__main__":
    app()
