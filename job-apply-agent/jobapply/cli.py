"""Command line interface."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import settings as settings_mod
from .db import init_db, session_scope

app = typer.Typer(add_completion=False, help="Get the right resume to the right job at the right time.")
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    _setup_logging(verbose)


# --------------------------------------------------------------------- setup


@app.command()
def init() -> None:
    """Create the database and check the configuration."""
    st = settings_mod.load(reload=True)
    init_db()
    console.print(f"[green]✓[/] database ready at [bold]{st.db_path}[/]")

    cfg_real = st.env.config_path.exists()
    console.print(f"{'[green]✓[/]' if cfg_real else '[yellow]![/]'} config: "
                  f"{st.env.config_path}{'' if cfg_real else ' (using config.example.yaml — copy it)'}")
    key_note = "set" if st.env.anthropic_api_key else (
        "missing — matching falls back to keywords and screenshots cannot be read"
    )
    console.print(f"{'[green]✓[/]' if st.env.anthropic_api_key else '[yellow]![/]'} "
                  f"ANTHROPIC_API_KEY {key_note}")

    from .browser import has_session
    sess_note = "saved" if has_session() else "missing — run `jobapply login`"
    console.print(f"{'[green]✓[/]' if has_session() else '[yellow]![/]'} LinkedIn session {sess_note}")

    from .resumes import ResumeRegistry
    reg = ResumeRegistry.load()
    missing = [r.key for r in reg if not r.exists()]
    console.print(f"[green]✓[/] {len(reg)} resume(s) registered"
                  + (f" [red]({len(missing)} file(s) missing: {', '.join(missing)})[/]" if missing else ""))
    console.print(f"\n  dry_run=[bold]{st.env.dry_run}[/]  require_review=[bold]{st.env.require_review}[/]"
                  f"  email_mode=[bold]{st.env.email_mode}[/]")


@app.command()
def login(
    site: str = typer.Option("linkedin", help="Session name, e.g. linkedin or workday"),
    url: str = typer.Option("https://www.linkedin.com/login", help="Page to open"),
) -> None:
    """Open a browser so you can sign in once; the session is then reused."""
    from .browser import interactive_login

    path = asyncio.run(interactive_login(state_name=site, url=url))
    console.print(f"[green]✓[/] session saved to {path}")


@app.command("gmail-auth")
def gmail_auth(headless: bool = typer.Option(False, help="Use the console OAuth flow")) -> None:
    """Authorise Gmail so the agent can create drafts."""
    from .mailer.gmail import GmailClient, GmailUnavailable

    try:
        GmailClient().authorize(headless=headless)
        console.print("[green]✓[/] Gmail authorised")
    except GmailUnavailable as exc:
        console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from exc


# ------------------------------------------------------------------ pipeline


@app.command()
def discover(
    headless: bool = typer.Option(True, help="Run the browser headless"),
    limit: int = typer.Option(0, help="Cap how many jobs are processed (0 = no cap)"),
) -> None:
    """Search LinkedIn, score every result, and queue what is worth applying to."""
    from . import pipeline

    init_db()
    report = asyncio.run(pipeline.discover(headless=headless, limit=limit or None))
    table = Table(title="Discovery", show_header=False, box=None)
    for key, value in report.as_dict().items():
        if key == "errors":
            continue
        table.add_row(key.replace("_", " "), str(value))
    console.print(table)
    for err in report.errors or []:
        console.print(f"[red]error[/] {err}")


@app.command("run-due")
def run_due(
    limit: int = typer.Option(5, help="Maximum applications this pass"),
    headless: bool = typer.Option(True),
    force: bool = typer.Option(False, "--force", help="Ignore scheduled times and run anyway"),
) -> None:
    """Apply to everything whose send time has arrived."""
    from . import pipeline

    init_db()
    results = asyncio.run(pipeline.run_due(limit=limit, headless=headless, force=force))
    if not results:
        console.print("[dim]nothing due right now[/]")
        return
    table = Table("id", "outcome", "message")
    for r in results:
        table.add_row(str(r["application_id"]), r["outcome"], (r.get("message") or "")[:90])
    console.print(table)


@app.command()
def ingest(
    folder: Path = typer.Option(None, help="Folder of screenshots (defaults to the configured inbox)"),
) -> None:
    """Read screenshots of LinkedIn posts and queue email applications."""
    from . import pipeline
    from .manual.watcher import ingest_directory

    init_db()
    results = ingest_directory(folder) if folder else pipeline.ingest_screenshots()
    if not results:
        console.print("[dim]no new screenshots[/]")
        return
    table = Table("status", "company", "role", "recipient", "score")
    for r in results:
        table.add_row(r.get("status", "?"), r.get("company", "—") or "—",
                      r.get("title", "—") or "—", r.get("recipient") or "—",
                      str(r.get("score", "")))
    console.print(table)


@app.command()
def watch(interval: float = typer.Option(15.0, help="Seconds between inbox polls")) -> None:
    """Watch the screenshot inbox and process drops as they land."""
    from .manual.watcher import ScreenshotWatcher

    init_db()
    console.print("[green]watching[/] — drop screenshots in the inbox (Ctrl-C to stop)")
    ScreenshotWatcher(interval_s=interval).run_forever()


@app.command()
def run(
    discover_minutes: int = typer.Option(90),
    apply_minutes: int = typer.Option(5),
    headless: bool = typer.Option(True),
    batch: int = typer.Option(3),
) -> None:
    """Run the full agent: discovery, timed sending, and screenshot ingest."""
    from .scheduler import run_forever

    run_forever(discover_minutes=discover_minutes, apply_minutes=apply_minutes,
                headless=headless, batch=batch)


@app.command()
def serve(
    host: str = typer.Option(None), port: int = typer.Option(None),
    reload: bool = typer.Option(False),
) -> None:
    """Start the dashboard."""
    import uvicorn

    st = settings_mod.load()
    host = host or st.env.web_host
    port = port or st.env.web_port
    console.print(f"[green]dashboard[/] http://{host}:{port}")
    uvicorn.run("jobapply.web.app:create_app", host=host, port=port,
                reload=reload, factory=True)


# ------------------------------------------------------------------ resumes

resumes_app = typer.Typer(help="Inspect and manage the resume registry.")
app.add_typer(resumes_app, name="resumes")


@resumes_app.command("list")
def resumes_list() -> None:
    """Show every registered resume."""
    from .resumes import ResumeRegistry

    reg = ResumeRegistry.load()
    table = Table("key", "role", "seniority", "file", "keywords")
    for r in reg:
        table.add_row(
            r.key + (" *" if r.key == reg.default_key else ""),
            r.role, r.seniority or "—",
            r.file + ("" if r.exists() else "  [red](missing)[/]"),
            str(len(r.keywords)),
        )
    console.print(table)


@resumes_app.command("render")
def resumes_render(all_: bool = typer.Option(False, "--all", help="Render every markdown resume")) -> None:
    """Pre-render markdown resumes to PDF."""
    from .render import render_markdown_pdf
    from .resumes import ResumeRegistry

    for r in ResumeRegistry.load():
        if r.fmt != "md" or not r.exists():
            continue
        out = render_markdown_pdf(r.path, force=all_)
        console.print(f"[green]✓[/] {r.key} → {out}")


@resumes_app.command("match")
def resumes_match(
    title: str = typer.Argument(..., help="Job title"),
    description: str = typer.Option("", help="Job description text"),
    file: Path = typer.Option(None, help="Read the description from a file instead"),
    llm: bool = typer.Option(True, help="Use Claude to re-rank"),
) -> None:
    """Ask which resume would be sent for a given job — without applying."""
    from .resumes import select_resume

    if file:
        description = file.read_text(encoding="utf-8")
    result = select_resume(title, description, use_llm=llm)
    console.print(f"\n[bold]{result.resume_key}[/]  score [bold]{result.score}[/]"
                  f"  (via {result.source}, seniority fit: {result.seniority_fit})\n")
    for reason in result.reasons:
        console.print(f"  • {reason}")
    if result.missing_skills:
        console.print(f"\n  [yellow]gaps:[/] {', '.join(result.missing_skills)}")
    console.print("\n  ranking: " + ", ".join(f"{k}={s}" for k, s in result.ranked))


@resumes_app.command("sync")
def resumes_sync() -> None:
    """Load the registry into the database."""
    from .models import ResumeRecord
    from .resumes import ResumeRegistry

    init_db()
    reg = ResumeRegistry.load()
    with session_scope() as sess:
        from sqlalchemy import select as sa_select

        existing = {r.key: r for r in sess.scalars(sa_select(ResumeRecord)).all()}
        for r in reg:
            row = existing.get(r.key) or ResumeRecord(key=r.key)
            row.role, row.path, row.fmt = r.role, str(r.path), r.fmt
            row.summary, row.keywords, row.seniority = r.summary, r.keywords, r.seniority
            sess.add(row)
    console.print(f"[green]✓[/] synced {len(reg)} resume(s)")


# ------------------------------------------------------------------- status


@app.command()
def status() -> None:
    """Show the current pipeline at a glance."""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from .models import Application, AppStatus, Job

    init_db()
    with session_scope() as sess:
        counts = dict(sess.execute(
            sa_select(Application.status, func.count(Application.id)).group_by(Application.status)
        ).all())
        upcoming = sess.execute(
            sa_select(Application, Job).join(Job, Application.job_id == Job.id)
            .where(Application.status == AppStatus.SCHEDULED)
            .order_by(Application.scheduled_for).limit(8)
        ).all()

        table = Table("status", "count", box=None)
        for status_val, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            table.add_row(status_val.value, str(n))
        console.print(table)

        if upcoming:
            console.print("\n[bold]Next out the door[/]")
            up = Table("when", "role", "company", "score", "resume")
            for a, j in upcoming:
                up.add_row(
                    a.scheduled_for.strftime("%d %b %H:%M") if a.scheduled_for else "—",
                    (j.title or "")[:42], (j.company or "")[:26],
                    f"{a.match_score:.0f}", a.resume_key or "—",
                )
            console.print(up)


@app.command()
def plan(
    title: str = typer.Argument(...),
    location: str = typer.Option("India"),
    posted_hours_ago: float = typer.Option(3.0),
    score: float = typer.Option(80.0),
) -> None:
    """Explain when an application would be sent, and why."""
    from datetime import datetime, timedelta

    from .timing import plan_send_time

    st = settings_mod.load()
    now = datetime.now(UTC)
    result = plan_send_time(
        posted_at=now - timedelta(hours=posted_hours_ago),
        location=location, cfg=st.config.timing, match_score=score, now=now,
    )
    console.print(f"\n[bold]{title}[/] — {location}")
    if result.skip:
        console.print("  [yellow]would not apply[/]")
    else:
        console.print(f"  send at : [bold]{result.send_at.astimezone().strftime('%a %d %b %H:%M %Z')}[/]")
        console.print(f"  priority: {result.priority}")
    console.print(f"  reason  : {result.reason}\n")


if __name__ == "__main__":
    app()
