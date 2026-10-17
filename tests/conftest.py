"""Async fixtures use real file-backed SQLite and independent sessions."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gh_maintainer.database import Database


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    instance = Database(tmp_path / "index.sqlite")
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()
