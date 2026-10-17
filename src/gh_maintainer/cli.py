"""Command-line entry points for sync, reports and local maintenance decisions."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import click
import uvicorn
from sqlalchemy.exc import SQLAlchemyError

from gh_maintainer.clock import parse_date
from gh_maintainer.database import Database, default_path
from gh_maintainer.demo import seed_demo
from gh_maintainer.github import PyGithubSource, resolve_token
from gh_maintainer.logging import configure_logging
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.report import markdown
from gh_maintainer.repositories import AnalysisService, TriageService
from gh_maintainer.schemas import (
    AnalysisInput,
    Contract,
    Disposition,
    ItemFilters,
    Kind,
    State,
    owner_name,
    repository_name,
)
from gh_maintainer.sync import SyncService
from gh_maintainer.web import create_app


def output(text: str) -> None:
    click.echo(text)


@click.group(
    name="gh-maintainer",
    context_settings={"help_option_names": ["-h", "--help"], "show_default": True},
    epilog="Start with sync to index GitHub, then use prs, issues, or repos offline.",
)
@click.version_option(package_name="gh-maintainer")
@click.option(
    "--db",
    type=click.Path(dir_okay=False),
    default=default_path,
    show_default="platform user data directory",
    envvar="GH_MAINTAINER_DB",
    show_envvar=True,
    help="SQLite index path (also accepted after the command).",
)
@click.pass_context
def main(ctx: click.Context, db: str) -> None:
    """Index and maintain GitHub repositories in SQLite.

    Sync reads GitHub; queries, analysis, and triage use the local index.
    """
    ctx.obj = db


@main.group("db", invoke_without_command=True)
@click.option(
    "--db",
    type=click.Path(dir_okay=False),
    help="Override the SQLite index path for this command.",
)
@click.pass_context
def db_command(ctx: click.Context, db: str | None) -> None:
    """Show the selected database path or delete the local index.

    No subcommand prints the path. Stop sync, watch, and serve before deleting.
    """
    ctx.obj = db or ctx.obj
    if ctx.invoked_subcommand is None:
        ctx.invoke(execute, command="db", db_action="path")


@db_command.command("path")
@click.option(
    "--db", type=click.Path(dir_okay=False), help="Override the SQLite index path."
)
@click.pass_context
def db_path(ctx: click.Context, db: str | None) -> None:
    """Print the absolute database path without opening or creating it."""
    ctx.invoke(execute, command="db", db=db, db_action="path")


@db_command.command("delete")
@click.option(
    "--db", type=click.Path(dir_okay=False), help="Override the SQLite index path."
)
@click.option(
    "--yes", is_flag=True, help="Confirm deletion, including all local decisions."
)
@click.pass_context
def db_delete(ctx: click.Context, db: str | None, yes: bool) -> None:
    """Delete the selected database and its SQLite sidecars.

    Stop sync, watch, and serve first. Requires --yes. This discards all indexed
    history, local notes, analysis, and processing state.
    """
    if not yes:
        raise click.UsageError(
            "Pass --yes to confirm deletion of the local index.", ctx
        )
    ctx.invoke(execute, command="db", db=db, db_action="delete")


@click.pass_context
def execute(ctx: click.Context, **options: object) -> None:
    """Bridge Click parameters to the async application lifecycle."""
    if ctx.info_name in {"sync", "watch"}:
        if options.get("repo") and options.get("owner"):
            raise click.UsageError("--repo and --owner cannot be combined.", ctx)
    if ctx.info_name == "changes" and options.get("since") and options.get("all"):
        raise click.UsageError("--since and --all cannot be combined.", ctx)
    if options.pop("unprocessed", False):
        if options.get("processing"):
            raise click.UsageError(
                "--unprocessed and --processing cannot be combined.", ctx
            )
        options["processing"] = "pending"
    options["db"] = options.get("db") or ctx.obj
    args = SimpleNamespace(command=options.pop("command", ctx.info_name), **options)
    try:
        if args.command == "serve":
            uvicorn.run(create_app(args.db), host=args.host, port=args.port)
            code = 0
        else:
            code = asyncio.run(run(args))
    except KeyboardInterrupt:
        ctx.exit(130)
    except (RuntimeError, ValueError, OSError, SQLAlchemyError) as error:
        raise click.ClickException(str(error)) from error
    ctx.exit(code)


def register_commands() -> None:
    """Share option contracts while keeping command help self-contained."""
    descriptions = {
        "sync": "Fetch issues, PRs, and conversations from GitHub into SQLite.",
        "watch": "Sync immediately, then repeat at a fixed interval.",
        "report": "Export the full portfolio as Markdown or JSON.",
        "serve": "Serve the local dashboard and read-only API.",
        "triage": "Save a local disposition and note without changing GitHub.",
        "demo": "Populate the index with sample data, without credentials.",
        "migrate": "Create or upgrade the SQLite database schema.",
        "prs": "List indexed pull requests (open by default).",
        "issues": "List indexed issues (open by default).",
        "show": "Show an item's body, coverage, analysis, and triage.",
        "comments": "Read an item's conversation, inline comments, and reviews.",
        "status": "List PRs in all states with signals and processing status.",
        "changes": "List observed activity since processing, or retained history.",
        "repos": "Show indexed repositories and synchronization coverage.",
        "classify": "Replace an item's saved local classification labels.",
        "summarize": "Save supplied summary TEXT; use - to read from stdin.",
        "rank": "Save a supplied rank SCORE; higher values sort first.",
        "mark-processed": "Acknowledge an item's current source revision.",
    }
    for name, summary in descriptions.items():
        description = summary
        params: list[click.Parameter] = [
            click.Option(
                ["--db"],
                type=click.Path(dir_okay=False),
                help="Override the SQLite index path for this command.",
            )
        ]
        if name in {"sync", "watch"}:
            params += [
                click.Option(
                    ["--full"], is_flag=True, help="Reconcile all historical items."
                ),
                click.Option(
                    ["--repo"],
                    multiple=True,
                    type=repository_name,
                    metavar="OWNER/REPO",
                    help="Sync directly (repeatable, across owners); excludes --owner.",
                ),
                click.Option(
                    ["--owner"],
                    type=owner_name,
                    metavar="LOGIN",
                    help="Sync this user or organization; excludes --repo.",
                ),
            ]
        if name == "watch":
            params.append(
                click.Option(
                    ["--interval"],
                    type=click.IntRange(min=60),
                    default=900,
                    help="Seconds between syncs.",
                )
            )
        if name == "report":
            params += [
                click.Option(
                    ["--format"],
                    type=click.Choice(["markdown", "json"]),
                    default="markdown",
                    help="Report output format.",
                ),
                click.Option(
                    ["--output"],
                    type=click.Path(dir_okay=False, path_type=Path),
                    help="Write to this file instead of stdout.",
                ),
            ]
        if name == "serve":
            params += [
                click.Option(["--host"], default="127.0.0.1", help="Address to bind."),
                click.Option(
                    ["--port"],
                    type=click.IntRange(1, 65535),
                    default=8765,
                    help="Port to listen on.",
                ),
            ]
        if name not in {"sync", "watch", "report", "serve", "demo", "migrate"}:
            params.append(
                click.Option(
                    ["--format"],
                    type=click.Choice(["text", "json"]),
                    default="text",
                    help="Output format; JSON is a single object.",
                )
            )
        if name in {"prs", "issues", "status", "comments", "changes"}:
            params += [
                click.Option(
                    ["--limit"],
                    type=click.IntRange(1, 500),
                    default=100,
                    help="Maximum records per page.",
                ),
                click.Option(
                    ["--offset"],
                    type=click.IntRange(min=0),
                    default=0,
                    help="Number of matching records to skip.",
                ),
            ]
        if name in {"prs", "issues", "status"}:
            params += [
                click.Option(
                    ["--repo"],
                    type=repository_name,
                    metavar="OWNER/REPO",
                    help="Filter by repository.",
                ),
                click.Option(
                    ["--state"],
                    type=click.Choice(["open", "closed", "merged", "all"]),
                    default="all" if name == "status" else "open",
                    help="Filter by GitHub state.",
                ),
                click.Option(["--search"], default="", help="Search item titles."),
                click.Option(
                    ["--disposition"],
                    type=click.Choice([v.value for v in Disposition]),
                    help="Filter by local triage disposition.",
                ),
                click.Option(
                    ["--processing"],
                    type=click.Choice(
                        ["unprocessed", "changed", "processed", "pending"]
                    ),
                    help="Filter by processing status; excludes --unprocessed.",
                ),
                click.Option(
                    ["--unprocessed"],
                    is_flag=True,
                    help="Include new items and items changed since processing.",
                ),
                click.Option(
                    ["--classification"], help="Filter by a current local label."
                ),
                click.Option(
                    ["--sort"],
                    type=click.Choice(["priority", "updated", "rank"]),
                    default="priority",
                    help="Order matching items.",
                ),
            ]
        if name in {
            "show",
            "comments",
            "classify",
            "summarize",
            "rank",
            "mark-processed",
            "triage",
            "changes",
        }:
            params.append(click.Argument(["item_id"], required=name != "changes"))
            description += (
                "\n\nITEM_ID accepts OWNER/REPO#NUMBER or the stored issue node ID."
            )
        if name in {"classify", "summarize", "rank", "mark-processed", "triage"}:
            params.append(
                click.Option(
                    ["--revision"],
                    type=click.IntRange(min=1),
                    help="Reject the write if the source revision has changed.",
                )
            )
        if name in {"classify", "summarize", "rank"}:
            params += [
                click.Option(
                    ["--producer"], default="manual", help="Result producer/version."
                ),
                click.Option(
                    ["--reason"], default="", help="Reason or supporting evidence."
                ),
            ]
        if name == "classify":
            params.append(click.Argument(["labels"], nargs=-1, required=True))
        if name == "summarize":
            params.append(click.Argument(["text"]))
        if name == "rank":
            params.append(click.Argument(["score"], type=float))
        if name == "mark-processed":
            params.append(
                click.Option(
                    ["--actor"], default="manual", help="Who reviewed the item."
                )
            )
        if name == "comments":
            params.append(
                click.Option(
                    ["--include-unavailable"],
                    is_flag=True,
                    help="Include records absent from the latest complete listing.",
                )
            )
        if name == "changes":
            params += [
                click.Option(
                    ["--since"],
                    type=parse_date,
                    metavar="TIMESTAMP",
                    help="Since an ISO timestamp with timezone; excludes --all.",
                ),
                click.Option(
                    ["--all"],
                    is_flag=True,
                    help="Include already acknowledged changes; excludes --since.",
                ),
            ]
        if name == "triage":
            params += [
                click.Argument(
                    ["disposition"], type=click.Choice([v.value for v in Disposition])
                ),
                click.Option(["--note"], default="", help="Local maintenance note."),
                click.Option(
                    ["--until"],
                    type=parse_date,
                    metavar="TIMESTAMP",
                    help="ISO timestamp with timezone, required when snoozing.",
                ),
            ]
        main.add_command(
            click.Command(name, help=description, params=params, callback=execute)
        )


register_commands()


def display(data: Contract, text: str, args: SimpleNamespace) -> None:
    output(data.model_dump_json(indent=2) if args.format == "json" else text)


async def read_command(database: Database, args: SimpleNamespace) -> None:
    async with database.sessions() as session:
        query = CatalogQueries(session)
        if args.command in {"prs", "issues", "status"}:
            filters = ItemFilters(
                kind=Kind.ISSUE if args.command == "issues" else Kind.PR,
                state=None if args.state == "all" else State(args.state),
                repo=args.repo or "",
                q=args.search,
                disposition=args.disposition,
                processing=args.processing,
                classification=args.classification,
                sort=args.sort,
                limit=args.limit,
                offset=args.offset,
            )
            page = await query.page(filters)
            lines = [
                "ITEM\tSTATE\tCHECKS\tREVIEW\tMERGEABLE\tRANK\tPROCESSING\tREV\tCOVERAGE\tTITLE"
            ]
            for item in page.items:
                coverage = (
                    "incomplete"
                    if item.repo_error or not item.comments_synced_at
                    else "current"
                )
                if not item.available or not item.present:
                    coverage = "unavailable"
                rank = (
                    str(item.ranking.score)
                    if item.ranking and not item.ranking.stale
                    else "-"
                )
                lines.append(
                    f"{item.repo}#{item.number}\t{item.state.value}\t{item.checks}\t"
                    f"{item.review}\t{item.mergeable}\t{rank}\t{item.processing_state}\t"
                    f"{item.revision}\t{coverage}\t{item.title}"
                )
            lines.append(
                f"{len(page.items)} of {page.total} items (offset {args.offset})."
            )
            display(page, "\n".join(lines), args)
        elif args.command == "show":
            detail = await query.show(args.item_id)
            item = detail.item
            lines = [
                f"{item.repo}#{item.number}: {item.title}",
                item.url,
                f"State: {item.state.value}; checks: {item.checks}; "
                f"review: {item.review}; "
                f"mergeable: {item.mergeable}",
                f"Revision: {item.revision}; processing: {item.processing_state}; "
                f"triage: {item.disposition.value}",
                f"Last checked: {item.checked_at or 'never'} "
                f"by {item.processed_by or '-'}",
                f"Author: {item.author}; labels: {', '.join(item.labels) or '-'}",
                f"Assignees: {', '.join(item.assignees) or '-'}; draft: {item.draft}",
                f"PR head: {(item.pr or {}).get('head') or '-'}",
                f"Comments synced: {item.comments_synced_at or 'never'}",
                f"Coverage error: {item.repo_error or 'none'}",
                "",
                str(item.raw.get("body") or ""),
            ]
            for name in ("summary", "classification", "ranking"):
                analysis = getattr(item, name)
                if analysis:
                    lines += [
                        "",
                        f"{name} ({'stale' if analysis.stale else 'current'}):",
                        analysis.model_dump_json(indent=2),
                    ]
            if item.note:
                lines += ["", f"Triage note: {item.note}"]
            display(detail, "\n".join(lines), args)
        elif args.command == "comments":
            comment_page = await query.comments(
                args.item_id, args.limit, args.offset, args.include_unavailable
            )
            lines = [
                f"Comments synced: {comment_page.comments_synced_at or 'never'}; "
                f"coverage error: {comment_page.coverage_error or 'none'}"
            ]
            for comment in comment_page.comments:
                lines += [
                    "",
                    f"{comment.id} [{comment.kind}] {comment.author or 'deleted user'} "
                    f"{comment.created_at or ''} (available={comment.available})",
                    comment.url,
                    comment.body,
                ]
                context = "; ".join(
                    f"{key}={comment.raw[key]}"
                    for key in ("state", "path", "line", "side", "in_reply_to_id")
                    if comment.raw.get(key) is not None
                )
                if context:
                    lines.append(context)
            lines.append(
                f"{len(comment_page.comments)} of {comment_page.total} comments "
                f"(offset {args.offset})."
            )
            display(comment_page, "\n".join(lines), args)
        elif args.command == "changes":
            change_page = await query.changes(
                args.item_id,
                args.since,
                not args.all and not args.since,
                args.limit,
                args.offset,
            )
            lines = ["ITEM ID\tREVISION\tOBSERVED\tCHANGE"]
            lines += [
                f"{change.item_id}\t{change.revision or '-'}\t"
                f"{change.observed_at}\t{change.kind}"
                for change in change_page.changes
            ]
            lines.append(
                f"{len(change_page.changes)} of {change_page.total} changes "
                f"(offset {args.offset})."
            )
            display(change_page, "\n".join(lines), args)
        else:
            overview = await query.overview()
            lines = ["REPOSITORY\tPRESENT\tITEMS SYNCED\tCOMMENTS SYNCED\tERROR"]
            lines += [
                f"{repo.name}\t{repo.present}\t{repo.synced_at or 'never'}\t"
                f"{repo.comments_synced_at or 'never'}\t{repo.error or '-'}"
                for repo in overview.repos
            ]
            display(overview, "\n".join(lines), args)


async def run(args: SimpleNamespace) -> int:
    if args.command in {"sync", "watch"}:
        configure_logging()
    if args.command == "db":
        if args.db == ":memory:":
            raise ValueError("An in-memory database has no file to manage.")
        path = Path(args.db).resolve()
        if args.db_action == "path":
            output(str(path))
        else:
            # Do not open SQLite: management must also work on missing/invalid files.
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            output(f"Deleted database: {path}")
        return 0
    database = Database(args.db)
    try:
        await database.initialize()
        if args.command in {"sync", "watch"}:
            source = PyGithubSource(await resolve_token())
            try:
                while True:
                    source.calls = 0
                    result = await SyncService(database, source).run(
                        args.full, set(args.repo) if args.repo else None, args.owner
                    )
                    output(result.model_dump_json(indent=2))
                    if args.command == "sync":
                        return 0 if result.status == "complete" else 1
                    await asyncio.sleep(args.interval)
            finally:
                await source.close()
        if args.command in {
            "prs",
            "issues",
            "status",
            "show",
            "comments",
            "changes",
            "repos",
        }:
            await read_command(database, args)
        elif args.command in {"classify", "summarize", "rank"}:
            analysis_input = AnalysisInput(
                producer=args.producer,
                reason=args.reason,
                labels=args.labels if args.command == "classify" else None,
                text=(sys.stdin.read() if args.text == "-" else args.text)
                if args.command == "summarize"
                else None,
                score=args.score if args.command == "rank" else None,
            )
            kind = {
                "classify": "classification",
                "summarize": "summary",
                "rank": "ranking",
            }[args.command]
            analysis_result = await AnalysisService(database).save(
                args.item_id, kind, analysis_input, args.revision
            )
            display(
                analysis_result,
                f"Saved {kind} for revision {analysis_result.source_revision}.",
                args,
            )
        elif args.command == "mark-processed":
            processed = await AnalysisService(database).mark_processed(
                args.item_id, args.revision, args.actor
            )
            display(processed, f"Marked revision {processed.revision} processed.", args)
        elif args.command == "triage":
            await TriageService(database).decide(
                args.item_id,
                Disposition(args.disposition),
                args.note,
                args.until,
                revision=args.revision,
            )
            if args.format == "json":
                async with database.sessions() as session:
                    output(
                        (
                            await CatalogQueries(session).show(args.item_id)
                        ).model_dump_json(indent=2)
                    )
            else:
                output("Local triage decision saved.")
        elif args.command == "report":
            async with database.sessions() as session:
                data = await CatalogQueries(session).portfolio()
            text = (
                data.model_dump_json(indent=2)
                if args.format == "json"
                else markdown(data)
            )
            if args.output:
                args.output.write_text(text, encoding="utf-8")
            else:
                output(text)
        elif args.command == "demo":
            await seed_demo(database)
            output(f"Demo data created. Run: gh-maintainer --db {args.db} serve")
        elif args.command == "migrate":
            output("Database schema is up to date.")
    finally:
        await database.close()
    return 0
