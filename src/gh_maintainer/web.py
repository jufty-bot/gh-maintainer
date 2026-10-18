"""Async dashboard routes and local triage over typed database queries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Route

from gh_maintainer.clock import parse_date
from gh_maintainer.database import Database
from gh_maintainer.mcp import MCPRuntime
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.report import markdown
from gh_maintainer.repositories import TriageService
from gh_maintainer.schemas import (
    Category,
    CommentPage,
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
    TriageUpdate,
)

STATIC = Path(__file__).parent / "static"


def create_app(db_path: str, database: Database | None = None) -> FastAPI:
    runtime = MCPRuntime(db_path, database)
    db = runtime.database
    mcp_app = runtime.server.http_app(
        path="/mcp", stateless_http=True, json_response=True
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Mounted applications need their lifespan entered by the parent app.
        async with runtime.lifespan(), mcp_app.lifespan(_app):
            yield

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
            )
        except ValueError as error:
            status = (
                404
                if str(error).startswith("Unknown item")
                or str(error) == "Unknown item ID."
                else (400 if request.method == "POST" else 422)
            )
            raise HTTPException(status, str(error)) from None
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
