"""Read-only HTTP views over the local index. Tokens never reach this service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from gh_maintainer.report import markdown, snapshot
from gh_maintainer.store import Store

STATIC = Path(__file__).parent / "static"


def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="GitHub Maintainer", docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def read() -> dict[str, Any]:
        store = Store(db_path)
        try:
            return snapshot(store)
        finally:
            store.close()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        data = read()
        return {key: value for key, value in data.items() if key != "items"}

    @app.get("/api/items")
    def items(
        q: str = "",
        kind: str = "",
        state: str = "",
        repo: str = "",
        queue: bool = False,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        rows = read()["items"]
        rows = [
            row
            for row in rows
            if (
                not q
                or q.lower()
                in (
                    f"{row['title']} {row['repo']} "
                    f"{' '.join(row['labels'])} {row['number']}"
                ).lower()
            )
            and (not kind or row["kind"] == kind)
            and (not state or row["state"] == state)
            and (not repo or row["repo"] == repo)
            and (not queue or row["actionable"])
        ]
        return {"total": len(rows), "items": rows[offset : offset + limit]}

    @app.get("/api/items/{item_id}")
    def item(item_id: str) -> dict[str, Any]:
        row = next((row for row in read()["items"] if row["id"] == item_id), None)
        if row is None:
            raise HTTPException(404, "Unknown item")
        store = Store(db_path)
        try:
            changes = [
                dict(change)
                for change in store.db.execute(
                    "SELECT * FROM changes WHERE item_id=? ORDER BY id DESC LIMIT 50",
                    (item_id,),
                )
            ]
        finally:
            store.close()
        return {"item": row, "changes": changes}

    @app.get("/api/report", response_class=PlainTextResponse)
    def report() -> str:
        return markdown(read())

    return app
