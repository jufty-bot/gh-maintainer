"""Command-line entry points for sync, reports and local maintenance decisions."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import uvicorn

from gh_maintainer.demo import seed_demo
from gh_maintainer.github import GitHub, resolve_token
from gh_maintainer.report import markdown, snapshot
from gh_maintainer.store import Store
from gh_maintainer.sync import now, parse_date, synchronize
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
    for name in ("sync", "watch", "report", "serve", "triage", "demo"):
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
    return root


def run(args: argparse.Namespace) -> int:
    if args.command == "serve":
        uvicorn.run(create_app(args.db), host=args.host, port=args.port)
        return 0
    store = Store(args.db)
    try:
        if args.command in {"sync", "watch"}:
            if args.command == "watch" and args.interval < 60:  # noqa: PLR2004
                raise ValueError("Watch interval must be at least 60 seconds.")
            github = GitHub(resolve_token())
            try:
                while True:
                    github.requests = 0
                    result = synchronize(
                        store,
                        github,
                        args.full,
                        set(args.repo) if args.repo else None,
                        progress,
                    )
                    output(json.dumps(result, indent=2))
                    if args.command == "sync":
                        return 0 if result["status"] == "complete" else 1
                    time.sleep(args.interval)
            finally:
                github.close()
        if args.command == "triage":
            until = None
            if args.disposition == "snoozed":
                if not args.until:
                    raise ValueError("Snoozing requires --until with a timezone.")
                until = parse_date(args.until).isoformat().replace("+00:00", "Z")
                if parse_date(until) <= parse_date(now()):
                    raise ValueError("Snooze deadline must be in the future.")
            store.triage(args.item_id, args.disposition, args.note, until, now())
            output("Local triage decision saved.")
        elif args.command == "report":
            data = snapshot(store)
            text = (
                json.dumps(data, indent=2) if args.format == "json" else markdown(data)
            )
            if args.output:
                args.output.write_text(text, encoding="utf-8")
            else:
                output(text)
        elif args.command == "demo":
            seed_demo(store)
            output(f"Demo data created. Run: gh-maintainer --db {args.db} serve")
    finally:
        store.close()
    return 0


def main() -> None:
    try:
        code = run(parser().parse_args())
    except KeyboardInterrupt:
        code = 130
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
        sys.stderr.write(f"Error: {error}\n")
        code = 1
    sys.exit(code)
