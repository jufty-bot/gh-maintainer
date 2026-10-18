"""Async dashboard routes and local triage over typed database queries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from gh_maintainer.database import Database
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.report import markdown
from gh_maintainer.repositories import TriageService
from gh_maintainer.schemas import (
    CommentPage,
    Disposition,
    ItemDetail,
    ItemFilters,
    ItemFocus,
    ItemPage,
    ItemSort,
    Kind,
    Overview,
    State,
    TriageUpdate,
)

STATIC = Path(__file__).parent / "static"


def create_app(db_path: str, database: Database | None = None) -> FastAPI:
    db = database or Database(db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await db.initialize()
        try:
            yield
        finally:
            if database is None:
                await db.close()

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

    @app.put("/api/items/{item_id}/triage")
    async def triage(
        item_id: str,
        decision: TriageUpdate,
        request: Request,
        connection: Annotated[AsyncSession, Depends(session)],
    ) -> ItemDetail:
        # A custom header forces cross-origin browsers to preflight. No CORS
        # permissions are granted, so other sites cannot modify the local index.
        if request.headers.get("x-maintainer-request") != "1" or (
            request.headers.get("sec-fetch-site") == "cross-site"
        ):
            raise HTTPException(403, "Triage requires a same-origin dashboard request")
        try:
            await TriageService(db).decide(
                item_id,
                decision.disposition,
                decision.note,
                decision.snoozed_until,
            )
        except ValueError as error:
            status = (
                404
                if str(error).startswith("Unknown item")
                or str(error) == "Unknown item ID."
                else 422
            )
            raise HTTPException(status, str(error)) from None
        detail = await CatalogQueries(connection).detail(item_id)
        if detail is None:
            raise HTTPException(404, "Unknown item")
        return detail

    @app.get("/api/report", response_class=PlainTextResponse)
    async def report(connection: Annotated[AsyncSession, Depends(session)]) -> str:
        return markdown(await CatalogQueries(connection).portfolio())

    return app
