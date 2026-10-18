"""HTTP and stdio MCP tools over the same services as the command-line interface."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from gh_maintainer.clock import now, parse_date
from gh_maintainer.config import SyncOptions
from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.github import (
    DEFAULT_CONCURRENCY,
    DEFAULT_REQUESTS_PER_SECOND,
    AsyncGitHubSource,
    resolve_token,
)
from gh_maintainer.logging import configure_logging
from gh_maintainer.mcp_commands import guard_policy, register_commands, tool_errors
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.report import markdown
from gh_maintainer.repositories import CatalogRepository, SyncLease, TriageService
from gh_maintainer.schemas import (
    Category,
    Contract,
    Disposition,
    ItemDetail,
    ItemFilters,
    ItemPage,
    Kind,
    Overview,
    Priority,
    State,
    SyncResult,
    owner_name,
    repository_name,
)
from gh_maintainer.sync import SyncService

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
LOCAL_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, open_world_hint=False
)


class WatchStatus(Contract):
    running: bool
    interval: int = 900
    full: bool = False
    repo: list[str] | None = None
    owner: str | None = None
    concurrency: int = DEFAULT_CONCURRENCY
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND
    last_result: SyncResult | None = None
    error: str | None = None


class MaintenanceTools:
    """Own the server's single background watcher; database leases fence writers."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._watch_task: asyncio.Task[None] | None = None
        self._watch_lock = asyncio.Lock()
        self._status = WatchStatus(running=False)

    async def sync(
        self,
        full: bool = False,
        repo: list[str] | None = None,
        owner: str | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
    ) -> SyncResult:
        options = SyncOptions(
            full=full,
            repo=repo,
            owner=owner,
            concurrency=concurrency,
            requests_per_second=requests_per_second,
        )
        if repo and owner:
            raise ValueError("repo and owner cannot be combined.")
        only = {repository_name(name) for name in repo} if repo else None
        owner = owner_name(owner) if owner else None
        source = AsyncGitHubSource(
            await resolve_token(),
            concurrency=options.concurrency,
            requests_per_second=options.requests_per_second,
        )
        try:
            return await SyncService(
                self.database, source, concurrency=concurrency
            ).run(full, only, owner)
        finally:
            await source.close()

    def watch_status(self) -> WatchStatus:
        return self._status.model_copy(
            update={
                "running": self._watch_task is not None and not self._watch_task.done()
            }
        )

    async def watch(
        self,
        interval: int = 900,
        full: bool = False,
        repo: list[str] | None = None,
        owner: str | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
    ) -> WatchStatus:
        if interval < 60:  # noqa: PLR2004
            raise ValueError("Watch interval must be at least 60 seconds.")
        async with self._watch_lock:
            if self.watch_status().running:
                raise ValueError(
                    "A watcher is already running; stop it before restarting."
                )
            self._status = WatchStatus(
                running=True,
                interval=interval,
                full=full,
                repo=repo,
                owner=owner,
                concurrency=concurrency,
                requests_per_second=requests_per_second,
            )
            self._watch_task = asyncio.create_task(self._watch())
            return self.watch_status()

    async def _watch(self) -> None:
        try:
            while True:
                result = await self.sync(
                    self._status.full,
                    self._status.repo,
                    self._status.owner,
                    self._status.concurrency,
                    self._status.requests_per_second,
                )
                self._status = self._status.model_copy(update={"last_result": result})
                await asyncio.sleep(self._status.interval)
        except Exception as error:
            # Like CLI watch, fatal errors stop the loop; partial syncs keep polling.
            self._status = self._status.model_copy(update={"error": str(error)})

    async def stop_watch(self) -> WatchStatus:
        async with self._watch_lock:
            if self._watch_task is not None:
                self._watch_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._watch_task
                self._watch_task = None
            return self.watch_status()


def register_tools(server: MCPServer[None], tools: MaintenanceTools) -> None:
    db = tools.database

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        )
    )
    @tool_errors
    async def sync(
        full: bool = False,
        repo: list[str] | None = None,
        owner: str | None = None,
        concurrency: Annotated[int, Field(ge=1)] = DEFAULT_CONCURRENCY,
        requests_per_second: Annotated[
            float, Field(gt=0, allow_inf_nan=False)
        ] = DEFAULT_REQUESTS_PER_SECOND,
    ) -> CallToolResult:
        """Sync GitHub into the index. repo limits item sync to owner/repository names.

        full forces historical reconciliation. Partial coverage returns isError=true
        with the complete SyncResult in structuredContent, just as CLI sync exits 1.
        GitHub credentials are resolved on the server only when syncing.
        """
        result = await tools.sync(full, repo, owner, concurrency, requests_per_second)
        return CallToolResult(
            content=[TextContent(type="text", text=result.model_dump_json(indent=2))],
            structured_content=result.model_dump(mode="json"),
            is_error=result.status != "complete",
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, open_world_hint=False
        )
    )
    @tool_errors
    async def report(
        format: Literal["markdown", "json"] = "markdown",  # noqa: A002
        output: str | None = None,
    ) -> str:
        """Export the portfolio as Markdown or JSON, matching CLI report.

        Returns the report text. Optional output also writes a UTF-8 file on the
        server, relative to its working directory; an existing file is overwritten.
        """
        async with db.sessions() as session:
            data = await CatalogQueries(session).portfolio()
        text = data.model_dump_json(indent=2) if format == "json" else markdown(data)
        if output:
            await asyncio.to_thread(Path(output).write_text, text, encoding="utf-8")
        return text

    @server.tool(annotations=LOCAL_WRITE)
    @tool_errors
    async def triage(
        item_id: str,
        disposition: Disposition,
        note: str = "",
        until: str | None = None,
        revision: Annotated[int, Field(ge=1)] | None = None,
        priority: Priority | None = None,
        category: Category | None = None,
        policy: str | None = None,
    ) -> str:
        """Save a local decision using an item's issue node ID; GitHub is unchanged.

        Snoozing requires a future ISO timestamp including a timezone in until.
        """
        await guard_policy(db, item_id, policy, "triage", disposition=disposition.value)
        await TriageService(db).decide(
            item_id,
            disposition,
            note,
            parse_date(until) if until else None,
            revision=revision,
            priority=priority,
            category=category,
        )
        return "Local triage decision saved."

    @server.tool(annotations=LOCAL_WRITE)
    @tool_errors
    async def demo() -> str:
        """Seed example data. Requires an empty index and no GitHub credential."""
        await seed_demo(db)
        return "Demo data created."

    @server.tool(annotations=LOCAL_WRITE)
    @tool_errors
    async def migrate() -> str:
        """Apply packaged database migrations (also run at server startup)."""
        await db.initialize()
        return "Database schema is up to date."

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, open_world_hint=False
        )
    )
    @tool_errors
    async def prune(
        keep: Annotated[int, Field(ge=0)] = 50,
        days: Annotated[int, Field(ge=0)] | None = None,
        vacuum: bool = False,
    ) -> int:
        """Prune observation history and return the number deleted, matching CLI prune.

        Keep at most keep observations per item.
        days additionally removes older observations. vacuum reclaims disk space.
        Source items and local triage decisions are retained.
        """
        older_than = now() - timedelta(days=days) if days is not None else None
        async with SyncLease(db) as lease:
            deleted = await CatalogRepository(db).prune_observations(
                keep_per_item=keep, older_than=older_than, lease=lease
            )
        if vacuum:
            await db.vacuum()
        return deleted

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        )
    )
    @tool_errors
    async def watch(
        interval: Annotated[int, Field(ge=60)] = 900,
        full: bool = False,
        repo: list[str] | None = None,
        owner: str | None = None,
        concurrency: Annotated[int, Field(ge=1)] = DEFAULT_CONCURRENCY,
        requests_per_second: Annotated[
            float, Field(gt=0, allow_inf_nan=False)
        ] = DEFAULT_REQUESTS_PER_SECOND,
    ) -> WatchStatus:
        """Start background sync immediately, then every interval seconds (minimum 60).

        Uses CLI watch's full/repo options. Only one watcher runs per server process.
        Returns immediately; inspect watch_status and call stop_watch to stop it.
        The watcher stops when the server shuts down. Use one server worker.
        """
        if repo and owner:
            raise ValueError("repo and owner cannot be combined.")
        return await tools.watch(
            interval, full, repo, owner, concurrency, requests_per_second
        )

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    async def watch_status() -> WatchStatus:
        """Read watcher activity, its last sync result and any fatal error."""
        return tools.watch_status()

    @server.tool(annotations=LOCAL_WRITE)
    @tool_errors
    async def stop_watch() -> WatchStatus:
        """Stop the watcher and await sync cleanup. Safe to repeat."""
        return await tools.stop_watch()

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    async def overview() -> Overview:
        """Read repository inventory, counts and the latest sync run from the index."""
        async with db.sessions() as session:
            return await CatalogQueries(session).overview()

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    async def list_items(
        q: str = "",
        kind: Kind | None = None,
        state: State | None = None,
        repo: str = "",
        queue: bool = False,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> ItemPage:
        """Search indexed items with SQL pagination, using the dashboard's filters."""
        filters = ItemFilters(
            q=q,
            kind=kind,
            state=state,
            repo=repo,
            queue=queue,
            limit=limit,
            offset=offset,
        )
        async with db.sessions() as session:
            return await CatalogQueries(session).page(filters)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    async def item_detail(item_id: str) -> ItemDetail:
        """Read an indexed item by issue node ID or OWNER/REPO#NUMBER reference."""
        async with db.sessions() as session:
            return await CatalogQueries(session).show(item_id)

    register_commands(server, db)


class MCPRuntime:
    """Share tool registration and resource ownership across both transports."""

    def __init__(self, db_path: str, database: Database | None = None) -> None:
        self.database = database or Database(db_path)
        self.owns_database = database is None
        self.tools = MaintenanceTools(self.database)
        self.server = MCPServer[None]("GitHub Maintainer")
        register_tools(self.server, self.tools)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        try:
            configure_logging()
            await self.database.initialize()
            try:
                yield
            finally:
                await self.tools.stop_watch()
        finally:
            if self.owns_database:
                await self.database.close()


async def run_stdio(db_path: str) -> None:
    """Serve MCP on stdin/stdout, reserving stdout for protocol messages."""
    runtime = MCPRuntime(db_path)
    async with runtime.lifespan():
        await runtime.server.run_stdio_async()
