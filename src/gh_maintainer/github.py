"""Typed GitHub source backed by PyGithub on a bounded SDK worker thread."""

from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import islice
from typing import Protocol, TypeVar

from github import Auth, Github, GithubException, GithubRetry
from github.GithubObject import GithubObject
from pydantic import TypeAdapter
from urllib3.response import BaseHTTPResponse

from gh_maintainer.schemas import (
    AccountSnapshot,
    ItemSnapshot,
    JsonObject,
    PullPayload,
    RepositoryPayload,
    RepositorySnapshot,
    SignalsPayload,
)

T = TypeVar("T")
JSON_OBJECT = TypeAdapter(JsonObject)


def raw_payload(obj: GithubObject) -> JsonObject:
    # The base public accessor returns the received payload. The completable
    # subclass accessor hydrates every list item with another HTTP request.
    descriptor: property = inspect.getattr_static(GithubObject, "raw_data")
    return JSON_OBJECT.validate_python(descriptor.__get__(obj))


class GitHubError(RuntimeError):
    """Credential-free source failure suitable for run logs and API responses."""


async def resolve_token() -> str:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token and token.strip():
        return token.strip()

    def from_cli() -> str:
        try:
            result = subprocess.run(
                ["gh", "auth", "token", "--hostname", "github.com"],  # noqa: S607
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            if result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        raise GitHubError("Set GH_TOKEN or GITHUB_TOKEN, or run gh auth login.")

    return await asyncio.to_thread(from_cli)


class GitHubSource(Protocol):
    calls: int

    async def account(self) -> AccountSnapshot: ...
    def repositories(self) -> AsyncIterator[list[RepositorySnapshot]]: ...
    def issues(
        self, repo: RepositorySnapshot, since: datetime | None
    ) -> AsyncIterator[list[ItemSnapshot]]: ...
    async def pull(self, repo: RepositorySnapshot, number: int) -> JsonObject: ...
    async def signals(self, ids: list[str]) -> dict[str, JsonObject]: ...


class BoundedGitHubRetry(GithubRetry):
    """Honor short Retry-After headers and defer long waits to the next sync."""

    def get_retry_after(self, response: BaseHTTPResponse) -> float | None:
        delay = super().get_retry_after(response)
        if delay is not None and delay > 30:  # noqa: PLR2004
            raise GitHubError("GitHub requested a retry wait longer than 30 seconds.")
        return delay


class PyGithubSource:
    """All lazy SDK iteration stays on one thread, away from the event loop."""

    def __init__(self, token: str, sdk: Github | None = None) -> None:
        self.sdk = sdk or Github(
            auth=Auth.Token(token),
            per_page=100,
            lazy=True,
            timeout=30,
            retry=BoundedGitHubRetry(total=3, backoff_factor=1, max_rate_limit_wait=30),
            api_version="2022-11-28",
            user_agent="gh-maintainer/0.1",
        )
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="github-sdk")
        self.calls = 0

    async def _call(self, function: Callable[[], T]) -> T:
        self.calls += 1
        try:
            return await asyncio.get_running_loop().run_in_executor(
                self.worker, function
            )
        except (GithubException, OSError) as error:
            status = error.status if isinstance(error, GithubException) else "network"
            message = f"GitHub source request failed ({status})."
            raise GitHubError(message) from None

        except AssertionError:
            raise GitHubError(
                "GitHub SDK rejected an unsafe or invalid request."
            ) from None

    async def close(self) -> None:
        await self._call(self.sdk.close)
        self.worker.shutdown(wait=False)

    async def account(self) -> AccountSnapshot:
        def load() -> AccountSnapshot:
            user = self.sdk.get_user()
            return AccountSnapshot(id=str(user.id), login=user.login)

        return await self._call(load)

    async def _batches(
        self, iterator: Iterator[GithubObject]
    ) -> AsyncIterator[list[JsonObject]]:
        while True:
            batch = await self._call(
                lambda: [raw_payload(obj) for obj in islice(iterator, 100)]
            )
            if not batch:
                return
            yield batch

    async def repositories(self) -> AsyncIterator[list[RepositorySnapshot]]:
        iterator = await self._call(
            lambda: iter(
                self.sdk.get_user().get_repos(affiliation="owner", sort="full_name")
            )
        )
        async for batch in self._batches(iterator):
            # Validate ownership fields before committing any inventory changes.
            for raw in batch:
                RepositoryPayload.model_validate(raw)
            yield [RepositorySnapshot.from_github(raw) for raw in batch]

    async def issues(
        self, repo: RepositorySnapshot, since: datetime | None
    ) -> AsyncIterator[list[ItemSnapshot]]:
        def iterator() -> Iterator[GithubObject]:
            repository = self.sdk.get_repo(repo.name)
            if since is None:
                return iter(
                    repository.get_issues(state="all", sort="updated", direction="asc")
                )
            return iter(
                repository.get_issues(
                    state="all", sort="updated", direction="asc", since=since
                )
            )

        items = await self._call(iterator)
        async for batch in self._batches(items):
            yield [ItemSnapshot.from_github(raw, repo.id) for raw in batch]

    async def pull(self, repo: RepositorySnapshot, number: int) -> JsonObject:
        raw = await self._call(
            lambda: JSON_OBJECT.validate_python(
                self.sdk.get_repo(repo.name).get_pull(number).raw_data
            )
        )
        PullPayload.model_validate(raw)
        return raw

    async def signals(self, ids: list[str]) -> dict[str, JsonObject]:
        result: dict[str, JsonObject] = {}
        batch_size = 20
        for offset in range(0, len(ids), batch_size):
            batch = ids[offset : offset + batch_size]

            def query(batch: list[str] = batch) -> JsonObject:
                _, response = self.sdk.requester.graphql_query(
                    """query($ids:[ID!]!){nodes(ids:$ids){... on PullRequest{
                    id number reviewDecision mergeable mergeStateStatus
                    commits(last:1){nodes{commit{oid statusCheckRollup{state}}}}}}}""",
                    {"ids": batch},
                )
                return JSON_OBJECT.validate_python(response)

            response = await self._call(query)
            data = response.get("data")
            nodes = data.get("nodes") if isinstance(data, dict) else None
            if (
                response.get("errors")
                or not isinstance(nodes, list)
                or len(nodes) != len(batch)
            ):
                raise GitHubError("GitHub returned incomplete pull request signals.")
            for node in nodes:
                raw = JSON_OBJECT.validate_python(node)
                signal = SignalsPayload.model_validate(raw)
                if not signal.id or signal.id not in batch:
                    raise GitHubError(
                        "GitHub returned an unexpected pull request signal."
                    )
                result[signal.id] = raw
            if not set(batch) <= result.keys():
                raise GitHubError("GitHub returned incomplete pull request signals.")
        return result
