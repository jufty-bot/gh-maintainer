"""Command-line entry points for sync, reports and local maintenance decisions."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

import uvicorn
from sqlalchemy.exc import SQLAlchemyError

from gh_maintainer.clock import now, parse_date
from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.github import PyGithubSource, resolve_token
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.report import markdown
from gh_maintainer.repositories import CatalogRepository, SyncLease, TriageService
from gh_maintainer.schemas import Disposition
from gh_maintainer.sync import SyncService
from gh_maintainer.web import create_app


def output(text: str) -> None:
    sys.stdout.write(text + "\n")


def progress(text: str) -> None:
    sys.stderr.write(text + "\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Index and maintain your owned GitHub repositories."
    )
    root.add_argument(
        "--db", default=os.getenv("GH_MAINTAINER_DB", ".data/maintainer.sqlite")
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
        "prune",
    ):
        command = commands.add_parser(name)
        command.add_argument("--db", default=argparse.SUPPRESS)
        if name in {"sync", "watch"}:
            command.add_argument(
                "--full", action="store_true", help="Reconcile all historical items."
            )
            command.add_argument(
                "--repo",
                action="append",
                help="Limit item sync to owner/repository (repeatable).",
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
        if name == "triage":
            command.add_argument(
                "item_id",
                help="GitHub issue node ID from the JSON report or item details.",
            )
            command.add_argument(
                "disposition",
                choices=("inbox", "planned", "waiting", "snoozed", "done"),
            )
            command.add_argument("--note", default="")
            command.add_argument(
                "--until", help="ISO timestamp with timezone, required when snoozing."
            )
        if name == "prune":
            command.add_argument(
                "--keep",
                type=int,
                default=50,
                help="Observations to keep per item (default: 50).",
            )
            command.add_argument(
                "--days",
                type=int,
                default=None,
                help="Prune observations older than N days.",
            )
            command.add_argument(
                "--vacuum",
                action="store_true",
                help="Reclaim free database space after pruning.",
            )
    return root


async def run(args: argparse.Namespace) -> int:
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
                        args.full, set(args.repo) if args.repo else None
                    )
                    output(result.model_dump_json(indent=2))
                    if args.command == "sync":
                        return 0 if result.status == "complete" else 1
                    await asyncio.sleep(args.interval)
            finally:
                await source.close()
        elif args.command == "triage":
            until = parse_date(args.until) if args.until else None
            await TriageService(database).decide(
                args.item_id, Disposition(args.disposition), args.note, until
            )
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
        elif args.command == "prune":
            older_than = (
                now() - timedelta(days=args.days) if args.days is not None else None
            )
            async with SyncLease(database) as lease:
                deleted = await CatalogRepository(database).prune_observations(
                    keep_per_item=args.keep,
                    older_than=older_than,
                    lease=lease,
                )
            if args.vacuum:
                await database.vacuum()
            output(f"Pruned {deleted} observation(s).")
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
