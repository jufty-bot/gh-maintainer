"""Command-line entry points for sync, reports and local maintenance decisions."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn
from sqlalchemy.exc import SQLAlchemyError

from gh_maintainer.clock import parse_date
from gh_maintainer.database import Database, default_path
from gh_maintainer.demo import seed_demo
from gh_maintainer.github import PyGithubSource, resolve_token
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
    sys.stdout.write(text + "\n")


def progress(text: str) -> None:
    sys.stderr.write(text + "\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Index and maintain GitHub repositories in SQLite."
    )
    root.add_argument(
        "--db",
        default=default_path(),
        help="SQLite path (GH_MAINTAINER_DB or the platform user data directory).",
    )
    commands = root.add_subparsers(dest="command", required=True)
    for name in (
        "sync",
        "watch",
        "report",
        "serve",
        "triage",
        "demo",
        "migrate",
        "prs",
        "issues",
        "show",
        "comments",
        "status",
        "changes",
        "repos",
        "classify",
        "summarize",
        "rank",
        "mark-processed",
        "db",
    ):
        command = commands.add_parser(name)
        command.add_argument("--db", default=argparse.SUPPRESS)
        if name == "db":
            actions = command.add_subparsers(dest="db_action")
            command.set_defaults(db_action="path")
            for action in ("path", "delete"):
                operation = actions.add_parser(action)
                operation.add_argument("--db", default=argparse.SUPPRESS)
                if action == "delete":
                    operation.add_argument(
                        "--yes",
                        action="store_true",
                        required=True,
                        help="Confirm deletion, including local analysis and triage.",
                    )
        if name in {"sync", "watch"}:
            command.add_argument(
                "--full", action="store_true", help="Reconcile all historical items."
            )
            selection = command.add_mutually_exclusive_group()
            selection.add_argument(
                "--repo",
                action="append",
                type=repository_name,
                help="Sync owner/repository directly (repeatable, across owners).",
            )
            selection.add_argument(
                "--owner",
                type=owner_name,
                help="Sync repositories belonging to this user or organization.",
            )
        if name == "watch":
            command.add_argument(
                "--interval",
                type=int,
                default=900,
                help="Seconds between syncs (minimum 60).",
            )
        if name == "report":
            command.add_argument(
                "--format", choices=("markdown", "json"), default="markdown"
            )
            command.add_argument("--output", type=Path)
        if name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8765)
        if name in {
            "prs",
            "issues",
            "status",
            "show",
            "comments",
            "changes",
            "repos",
            "classify",
            "summarize",
            "rank",
            "mark-processed",
            "triage",
        }:
            command.add_argument("--format", choices=("text", "json"), default="text")
        if name in {"prs", "issues", "status", "comments", "changes"}:
            command.add_argument("--limit", type=int, default=100)
            command.add_argument("--offset", type=int, default=0)
        if name in {"prs", "issues", "status"}:
            command.add_argument("--repo", type=repository_name)
            command.add_argument(
                "--state",
                choices=("open", "closed", "merged", "all"),
                default="all" if name == "status" else "open",
            )
            command.add_argument("--search", default="")
            command.add_argument(
                "--disposition", choices=[value.value for value in Disposition]
            )
            command.add_argument(
                "--processing",
                choices=("unprocessed", "changed", "processed", "pending"),
            )
            command.add_argument(
                "--unprocessed",
                dest="processing",
                action="store_const",
                const="pending",
                help="Include new items and items changed since processing.",
            )
            command.add_argument("--classification")
            command.add_argument(
                "--sort", choices=("priority", "updated", "rank"), default="priority"
            )
        if name in {
            "show",
            "comments",
            "classify",
            "summarize",
            "rank",
            "mark-processed",
        }:
            command.add_argument(
                "item_id", help="owner/repository#number or issue node ID."
            )
        if name in {"classify", "summarize", "rank", "mark-processed", "triage"}:
            command.add_argument(
                "--revision", type=int, help="Reject the write if the item has changed."
            )
        if name in {"classify", "summarize", "rank"}:
            command.add_argument("--producer", default="manual")
            command.add_argument("--reason", default="")
        if name == "classify":
            command.add_argument("labels", nargs="+")
        if name == "summarize":
            command.add_argument("text", help="Summary text, or - to read from stdin.")
        if name == "rank":
            command.add_argument("score", type=float, help="Higher values sort first.")
        if name == "mark-processed":
            command.add_argument("--actor", default="manual")
        if name == "comments":
            command.add_argument("--include-unavailable", action="store_true")
        if name == "changes":
            command.add_argument("item_id", nargs="?")
            selection = command.add_mutually_exclusive_group()
            selection.add_argument("--since", type=parse_date)
            selection.add_argument(
                "--all",
                action="store_true",
                help="Include already acknowledged changes.",
            )
        if name == "triage":
            command.add_argument(
                "item_id",
                help="owner/repository#number or issue node ID.",
            )
            command.add_argument(
                "disposition",
                choices=("inbox", "planned", "waiting", "snoozed", "done"),
            )
            command.add_argument("--note", default="")
            command.add_argument(
                "--until", help="ISO timestamp with timezone, required when snoozing."
            )
    return root


def display(data: Contract, text: str, args: argparse.Namespace) -> None:
    output(data.model_dump_json(indent=2) if args.format == "json" else text)


async def read_command(database: Database, args: argparse.Namespace) -> None:
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


async def run(args: argparse.Namespace) -> int:
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
            if args.command == "watch" and args.interval < 60:  # noqa: PLR2004
                raise ValueError("Watch interval must be at least 60 seconds.")
            source = PyGithubSource(await resolve_token())
            try:
                while True:
                    source.calls = 0
                    result = await SyncService(database, source, progress).run(
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
            until = parse_date(args.until) if args.until else None
            await TriageService(database).decide(
                args.item_id,
                Disposition(args.disposition),
                args.note,
                until,
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


def main() -> None:
    try:
        args = parser().parse_args()
        if args.command == "serve":
            uvicorn.run(create_app(args.db), host=args.host, port=args.port)
            code = 0
        else:
            code = asyncio.run(run(args))
    except KeyboardInterrupt:
        code = 130
    except (RuntimeError, ValueError, OSError, SQLAlchemyError) as error:
        sys.stderr.write(f"Error: {error}\n")
        code = 1
    sys.exit(code)
