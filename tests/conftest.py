"""Shared fixtures close every temporary database deterministically."""

from collections.abc import Iterator

import pytest

from gh_maintainer.store import Store


@pytest.fixture
def store() -> Iterator[Store]:
    instance = Store(":memory:")
    yield instance
    instance.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
