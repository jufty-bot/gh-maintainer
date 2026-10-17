"""Async read-only HTTP routes over typed, paginated database queries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from gh_maintainer.database import Database
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.report import markdown
from gh_maintainer.schemas import (
    ItemDetail,
    ItemFilters,
    ItemPage,
    Kind,
    Overview,
    State,
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

    app = FastAPI(title="GitHub Maintainer", docs_url="/api/docs", lifespan=lifespan)
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

    @app.get("/api/report", response_class=PlainTextResponse)
    async def report(connection: Annotated[AsyncSession, Depends(session)]) -> str:
        return markdown(await CatalogQueries(connection).portfolio())

    return app
