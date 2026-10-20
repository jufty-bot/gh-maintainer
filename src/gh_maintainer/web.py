"""Async dashboard routes and local triage over typed database queries."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import model_validator
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Route

from gh_maintainer.clock import parse_date
from gh_maintainer.config import Config, SyncOptions
from gh_maintainer.database import Database
from gh_maintainer.github import GitHubError, resolve_token
from gh_maintainer.mcp import MaintenanceTools, MCPRuntime
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.report import markdown
from gh_maintainer.repositories import TriageService
from gh_maintainer.schemas import (
    ActivityPage,
    Category,
    CommentPage,
    Contract,
    Disposition,
    ItemDetail,
    ItemFilters,
    ItemFocus,
    ItemPage,
    ItemSort,
    Kind,
    Overview,
    Priority,
    State,
    SyncResult,
    TriageUpdate,
    owner_name,
    repository_name,
)

STATIC = Path(__file__).parent / "static"


class DashboardSyncRequest(SyncOptions):
    @model_validator(mode="after")
    def selection(self) -> "DashboardSyncRequest":
        if self.repo is not None:
            if not self.repo:
                raise ValueError("Enter at least one owner/repository name.")
            self.repo = list(
                dict.fromkeys(repository_name(name.strip()) for name in self.repo)
            )
        if self.owner is not None:
            self.owner = owner_name(self.owner.strip())
        if self.owner and self.repo:
            raise ValueError("Choose an owner or explicit repositories, not both.")
        return self


class DashboardSyncStatus(Contract):
    state: Literal["idle", "running", "complete", "partial", "failed", "cancelled"] = (
        "idle"
    )
    message: str = "Ready to sync."
    stages: int = 0
    result: SyncResult | None = None
    options: DashboardSyncRequest


class SetupStatus(Contract):
    credential_available: bool
    message: str
    sync: DashboardSyncStatus


class DashboardSync:
    """One dashboard sync task, reusing the server's source and lease lifecycle."""

    def __init__(self, tools: MaintenanceTools, config: Config) -> None:
        self.tools = tools
        self.task: asyncio.Task[None] | None = None
        self.status = DashboardSyncStatus(
            options=DashboardSyncRequest.model_validate(
                config.command_defaults().get("sync", {})
            )
        )

    def start(self, options: DashboardSyncRequest) -> DashboardSyncStatus:
        if (
            self.task is not None and not self.task.done()
        ) or self.tools.watch_status().running:
            raise HTTPException(
                409, "A sync or watcher is already running. Wait for it to finish."
            )
        self.status = DashboardSyncStatus(
            state="running", message="Starting sync…", options=options
        )
        self.task = asyncio.create_task(self._run(options))
        return self.status.model_copy(deep=True)

    async def _run(self, options: DashboardSyncRequest) -> None:
        async def progress(message: str) -> None:
            self.status = self.status.model_copy(
                update={"message": message, "stages": self.status.stages + 1}
            )

        try:
            result = await self.tools.sync(**options.model_dump(), progress=progress)
            message = (
                f"Synced {result.synced} of {result.discovered} repositories."
                if result.status == "complete"
                else "Some repositories could not be synced. Check errors and retry."
            )
            self.status = self.status.model_copy(
                update={"state": result.status, "result": result, "message": message}
            )
        except asyncio.CancelledError:
            self.status = self.status.model_copy(
                update={
                    "state": "cancelled",
                    "message": "Sync cancelled. Completed repositories were retained; "
                    "retry to continue.",
                }
            )
            raise
        except Exception as error:
            self.status = self.status.model_copy(
                update={"state": "failed", "message": str(error)}
            )

    async def close(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


def dashboard_request(request: Request) -> None:
    if (
        request.headers.get("sec-fetch-site") == "cross-site"
        or request.headers.get("x-maintainer-request") != "1"
    ):
        raise HTTPException(403, "Requires a same-origin dashboard request")


def create_app(
    db_path: str, database: Database | None = None, config: Config | None = None
) -> FastAPI:
    runtime = MCPRuntime(db_path, database, config)
    db = runtime.database
    syncing = DashboardSync(runtime.tools, config or Config())
    mcp_app = runtime.server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=False, json_response=False
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # The parent app owns the SDK session manager and shared database lifecycle.
        async with runtime.lifespan(), runtime.server.session_manager.run():
            try:
                yield
            finally:
                await syncing.close()

    app = FastAPI(title="gh-maintainer", docs_url="/api/docs", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    async def session() -> AsyncIterator[AsyncSession]:
        async with db.sessions() as connection:
            yield connection

    # Module-visible annotation avoids unresolved closure aliases in FastAPI inspection.
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/overview")
    async def overview(
        connection: Annotated[AsyncSession, Depends(session)],
    ) -> Overview:
        return await CatalogQueries(connection).overview()

    @app.get("/api/setup")
    async def setup() -> SetupStatus:
        try:
            await resolve_token()
            available, message = (
                True,
                "Server credential available. Access is verified during sync.",
            )
        except GitHubError:
            available, message = (
                False,
                "On the server, run gh auth login or set GH_TOKEN / GITHUB_TOKEN, "
                "then check again.",
            )
        return SetupStatus(
            credential_available=available, message=message, sync=syncing.status
        )

    @app.get("/api/sync")
    async def sync_status() -> DashboardSyncStatus:
        return syncing.status

    @app.post("/api/sync", status_code=202)
    async def start_sync(
        options: DashboardSyncRequest, request: Request
    ) -> DashboardSyncStatus:
        dashboard_request(request)
        return syncing.start(options)

    @app.post("/api/sync/cancel")
    async def cancel_sync(request: Request) -> DashboardSyncStatus:
        dashboard_request(request)
        await syncing.close()
        return syncing.status

    @app.get("/api/items")
    async def items(
        connection: Annotated[AsyncSession, Depends(session)],
        q: str = "",
        kind: str = "",
        state: str = "",
        repo: str = "",
        queue: bool = False,
        disposition: str = "",
        sort: ItemSort = ItemSort.PRIORITY,
        focus: str = "",
        author: str = "",
        since: str = "",
        until: str = "",
        priority: str = "",
        category: str = "",
        triaged: bool | None = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> ItemPage:
        try:
            filters = ItemFilters(
                q=q,
                kind=Kind(kind) if kind else None,
                state=State(state) if state else None,
                repo=repo,
                queue=queue,
                disposition=Disposition(disposition) if disposition else None,
                sort=sort,
                focus=ItemFocus(focus) if focus else None,
                author=author,
                since=parse_date(since) if since else None,
                until=parse_date(until) if until else None,
                priority=Priority(priority) if priority else None,
                category=Category(category) if category else None,
                triaged=triaged,
                limit=limit,
                offset=offset,
            )
        except ValueError:
            raise HTTPException(422, "Invalid item filter") from None
        return await CatalogQueries(connection).page(filters)

    @app.get("/api/items/{item_id}")
    async def item(
        item_id: str, connection: Annotated[AsyncSession, Depends(session)]
    ) -> ItemDetail:
        detail = await CatalogQueries(connection).detail(item_id)
        if detail is None:
            raise HTTPException(404, "Unknown item")
        return detail

    @app.get("/api/items/{item_id}/comments")
    async def comments(
        item_id: str,
        connection: Annotated[AsyncSession, Depends(session)],
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> CommentPage:
        try:
            return await CatalogQueries(connection).comments(item_id, limit, offset)
        except ValueError:
            raise HTTPException(404, "Unknown item") from None

    @app.get("/api/items/{item_id}/follow-up")
    async def follow_up(
        item_id: str,
        connection: Annotated[AsyncSession, Depends(session)],
        revision: int = Query(ge=1),
        limit: int = Query(20, ge=1, le=500),
        offset: int = Query(0, ge=0),
        through_revision: int | None = Query(None, ge=1),
    ) -> ActivityPage:
        try:
            return await CatalogQueries(connection).follow_up(
                item_id, revision, limit, offset, through_revision
            )
        except ValueError as error:
            raise HTTPException(
                404 if str(error).startswith("Unknown item") else 422, str(error)
            ) from None

    @app.api_route("/api/items/{item_id}/triage", methods=["PUT", "POST"])
    async def triage(
        item_id: str,
        decision: TriageUpdate,
        request: Request,
        connection: Annotated[AsyncSession, Depends(session)],
    ) -> ItemDetail | dict[str, str]:
        # A custom header forces cross-origin browsers to preflight. No CORS
        # permissions are granted, so other sites cannot modify the local index.
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Triage requires a same-origin dashboard request")
        if (
            request.method == "PUT"
            and request.headers.get("x-maintainer-request") != "1"
        ):
            raise HTTPException(403, "Triage requires a same-origin dashboard request")
        try:
            await TriageService(db).decide(
                item_id,
                decision.disposition,
                decision.note,
                priority=decision.priority,
                category=decision.category,
                until=decision.snoozed_until,
                revision=decision.revision,
                finish_review=decision.finish_review,
                actor=decision.actor,
            )
        except ValueError as error:
            status = (
                404
                if str(error).startswith("Unknown item")
                or str(error) == "Unknown item ID."
                else (
                    409
                    if "Item changed" in str(error)
                    else (400 if request.method == "POST" else 422)
                )
            )
            raise HTTPException(status, str(error)) from None
        except SQLAlchemyError as error:
            conflict = (
                isinstance(error, OperationalError)
                and "database is locked" in str(error.orig).lower()
            )
            raise HTTPException(
                409 if conflict else 503,
                "The index changed during this save. Reload and review it again."
                if conflict
                else "Could not save to the index. Your edits are kept; retry later.",
            ) from error
        if request.method == "POST":
            return {"status": "ok", "item_id": item_id}
        detail = await CatalogQueries(connection).detail(item_id)
        if detail is None:
            raise HTTPException(404, "Unknown item")
        return detail

    @app.get("/api/report", response_class=PlainTextResponse)
    async def report(connection: Annotated[AsyncSession, Depends(session)]) -> str:
        return markdown(await CatalogQueries(connection).portfolio())

    # Dispatch only /mcp to the ASGI app. A catch-all mount would turn REST 405s
    # into MCP 404s, while a /mcp mount would redirect the endpoint to /mcp/.
    app.router.routes.append(Route("/mcp", endpoint=mcp_app))
    return app
