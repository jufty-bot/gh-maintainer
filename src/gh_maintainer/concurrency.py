"""Bounded async workers with ordered results and structured cleanup on failure."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def map_concurrently(
    function: Callable[[T], Awaitable[R]], items: Sequence[T], limit: int
) -> list[R]:
    if limit < 1:
        raise ValueError("Concurrency must be at least 1.")
    pending = iter(enumerate(items))
    results: dict[int, R] = {}

    async def worker() -> None:
        for index, item in pending:
            results[index] = await function(item)

    tasks = [asyncio.create_task(worker()) for _ in range(min(limit, len(items)))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return [results[index] for index in range(len(items))]
