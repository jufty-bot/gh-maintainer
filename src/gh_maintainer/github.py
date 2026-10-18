"""Typed GitHub source using pooled, bounded async HTTP requests."""

from __future__ import annotations

import asyncio
import math
import os
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import quote

import httpx
from aiolimiter import AsyncLimiter
from pydantic import TypeAdapter

from gh_maintainer.schemas import (
    AccountSnapshot,
    CommentSnapshot,
    Commit,
    ItemSnapshot,
    JsonObject,
    PullPayload,
    RepositoryActivity,
    RepositorySnapshot,
    SignalsPayload,
)

JSON_OBJECT = TypeAdapter(JsonObject)
JSON_OBJECTS = TypeAdapter(list[JsonObject])
DEFAULT_CONCURRENCY = 4
DEFAULT_REQUESTS_PER_SECOND = 2.0
SIGNAL_BATCH_SIZE = 20
LOW_BUDGET_FRACTION = 0.1
MAX_WAIT = 30
MAX_RETRIES = 3
API_URL = httpx.URL("https://api.github.com")


class GitHubError(RuntimeError):
    """Credential-free source failure suitable for run logs and API responses."""


@dataclass
class RateBudget:
    remaining: int
    limit: int
    reset_at: float
    next_at: float = 0.0

    def interval(self) -> float:
        if self.remaining > self.limit * LOW_BUDGET_FRACTION:
            return 0.0
        return max(0.0, self.reset_at - time.time()) / max(1, self.remaining)


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

    async def account(self, login: str | None = None) -> AccountSnapshot: ...
    async def repository(self, name: str) -> RepositorySnapshot: ...
    async def issue(self, repo: RepositorySnapshot, number: int) -> ItemSnapshot: ...
    def comments(
        self, repo: RepositorySnapshot, since: datetime | None
    ) -> AsyncIterator[list[CommentSnapshot]]: ...
    def reviews(
        self, repo: RepositorySnapshot, number: int
    ) -> AsyncIterator[list[CommentSnapshot]]: ...
    def repositories(
        self, owner: AccountSnapshot | None = None
    ) -> AsyncIterator[list[RepositorySnapshot]]: ...
    def issues(
        self, repo: RepositorySnapshot, since: datetime | None
    ) -> AsyncIterator[list[ItemSnapshot]]: ...
    async def pull(self, repo: RepositorySnapshot, number: int) -> JsonObject: ...
    async def signals(self, ids: list[str]) -> dict[str, JsonObject]: ...
    async def repository_activity(
        self, repos: list[RepositorySnapshot]
    ) -> dict[str, RepositoryActivity]: ...


class AsyncGitHubSource:
    """One reusable client; retries and rate-limit cooldowns never block the loop."""

    def __init__(
        self,
        token: str,
        concurrency: int = DEFAULT_CONCURRENCY,
        *,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("Concurrency must be at least 1.")
        if not math.isfinite(requests_per_second) or requests_per_second <= 0:
            raise ValueError("Requests per second must be a positive finite number.")
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "gh-maintainer/0.1",
            },
            timeout=30,
            limits=httpx.Limits(
                max_connections=concurrency, max_keepalive_connections=concurrency
            ),
            transport=transport,
            follow_redirects=False,
        )
        self.slots = asyncio.Semaphore(concurrency)
        # Capacity one prevents the initial burst permitted by larger buckets.
        self.pacer = AsyncLimiter(1, 1 / requests_per_second)
        self.admission_lock = asyncio.Lock()
        self.budgets: dict[str, RateBudget] = {}
        self.resume_at = 0.0
        self.calls = 0
        self.authenticated: AccountSnapshot | None = None

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _safe_url(url: httpx.URL) -> httpx.URL:
        if (
            url.scheme != API_URL.scheme
            or url.host != API_URL.host
            or url.port != API_URL.port
            or url.userinfo
        ):
            raise GitHubError("GitHub returned an unsafe pagination or redirect URL.")
        return url

    @staticmethod
    def _rate_limited(response: httpx.Response) -> bool:
        if response.status_code == 429:  # noqa: PLR2004
            return True
        if response.status_code == 403 and (  # noqa: PLR2004
            response.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in response.headers
        ):
            return True
        if response.status_code != 403 and response.request.url.path != "/graphql":  # noqa: PLR2004
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        errors = payload.get("errors", [])
        entries = [payload, *(errors if isinstance(errors, list) else [])]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            extensions = entry.get("extensions")
            code = extensions.get("code") if isinstance(extensions, dict) else None
            message = entry.get("message")
            if (
                entry.get("type") == "RATE_LIMITED"
                or code == "RATE_LIMITED"
                or (isinstance(message, str) and "rate limit" in message.lower())
            ):
                return True
        return False

    @staticmethod
    def _delay(response: httpx.Response) -> float | None:
        delays = []
        retry_after = response.headers.get("retry-after")
        try:
            if retry_after is not None:
                try:
                    delays.append(float(retry_after))
                except ValueError:
                    delays.append(
                        parsedate_to_datetime(retry_after).timestamp() - time.time()
                    )
            if response.headers.get("x-ratelimit-remaining") == "0":
                delays.append(
                    float(response.headers["x-ratelimit-reset"]) - time.time()
                )
        except (ValueError, TypeError, KeyError, OverflowError):
            raise GitHubError("GitHub returned invalid rate-limit headers.") from None
        if delays:
            if not all(math.isfinite(delay) for delay in delays):
                raise GitHubError("GitHub returned invalid rate-limit headers.")
            return max(0.0, *delays)
        # GitHub asks clients to wait at least a minute for secondary rate limits
        # without a Retry-After header. Defer those to the next sync.
        if AsyncGitHubSource._rate_limited(response):
            return 60
        return None

    def _observe_budget(self, response: httpx.Response, resource: str) -> None:
        headers = response.headers
        if not all(
            name in headers
            for name in (
                "x-ratelimit-limit",
                "x-ratelimit-remaining",
                "x-ratelimit-reset",
            )
        ):
            return
        try:
            remaining = int(headers["x-ratelimit-remaining"])
            limit = int(headers["x-ratelimit-limit"])
            reset = float(headers["x-ratelimit-reset"])
            if remaining < 0 or limit <= 0 or not math.isfinite(reset):
                raise ValueError
        except ValueError:
            raise GitHubError("GitHub returned invalid rate-limit headers.") from None
        previous = self.budgets.get(resource)
        if previous and reset < previous.reset_at:
            return
        budget = RateBudget(remaining, limit, reset)
        if previous and reset == previous.reset_at:
            # Concurrent responses may arrive out of order. A stale response must
            # never replenish the allowance or release an existing pacing delay.
            budget.remaining = min(remaining, previous.remaining)
            budget.next_at = previous.next_at
        budget.next_at = max(budget.next_at, time.monotonic() + budget.interval())
        self.budgets[resource] = budget

    def _budget_delay(self, resource: str) -> float:
        budget = self.budgets.get(resource)
        if budget is None or budget.reset_at <= time.time():
            return 0.0
        return max(0.0, budget.next_at - time.monotonic())

    async def _admit(self, resource: str) -> None:
        # Serialize admission, not network I/O. Recheck server feedback after any
        # wait so queued workers neither bypass a cooldown nor burst on resumption.
        async with self.admission_lock:
            while True:
                await self._cooldown()
                delay = self._budget_delay(resource)
                if delay > MAX_WAIT:
                    raise GitHubError(
                        "GitHub budget requires a wait longer than 30 seconds."
                    )
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                await self.pacer.acquire()
                if (
                    self.resume_at > time.monotonic()
                    or self._budget_delay(resource) > 0
                ):
                    continue
                budget = self.budgets.get(resource)
                if budget:
                    budget.next_at = time.monotonic() + budget.interval()
                return

    async def _cooldown(self) -> None:
        while (delay := self.resume_at - time.monotonic()) > 0:
            if delay > MAX_WAIT:
                raise GitHubError(
                    "GitHub requested a retry wait longer than 30 seconds."
                )
            await asyncio.sleep(delay)

    async def _request(
        self,
        method: str,
        path: str | httpx.URL,
        *,
        params: dict[str, str | int] | None = None,
        body: JsonObject | None = None,
    ) -> httpx.Response:
        url = self._safe_url(API_URL.join(path))
        resource = "graphql" if url.path == "/graphql" else "core"
        redirects = 0
        attempt = 0
        while True:
            async with self.slots:
                await self._admit(resource)
                self.calls += 1
                try:
                    response = await self.client.request(
                        method, url, params=params, json=body
                    )
                except (httpx.HTTPError, OSError):
                    if attempt >= MAX_RETRIES:
                        raise GitHubError(
                            "GitHub source request failed (network)."
                        ) from None
                    response = None
                if response is not None:
                    self._observe_budget(response, resource)
                    limited = self._rate_limited(response)
                    delay = self._delay(response)
                    if delay is not None:
                        self.resume_at = max(self.resume_at, time.monotonic() + delay)
                    if response.is_redirect:
                        if redirects >= 5 or "location" not in response.headers:  # noqa: PLR2004
                            raise GitHubError("GitHub returned an invalid redirect.")
                        url = self._safe_url(
                            response.url.join(response.headers["location"])
                        )
                        params = None
                        redirects += 1
                        continue
                    if response.is_success and not limited:
                        return response
                    retryable = (
                        limited
                        or response.status_code in {500, 502, 503, 504}
                        or (
                            response.status_code == 403 and delay is not None  # noqa: PLR2004
                        )
                    )
                    if not retryable or attempt >= MAX_RETRIES:
                        message = (
                            f"GitHub source request failed ({response.status_code})."
                        )
                        if limited:
                            message = (
                                "GitHub rate limit persisted after bounded retries."
                            )
                        raise GitHubError(message)
            await self._cooldown()
            await asyncio.sleep(2**attempt)
            attempt += 1

    @staticmethod
    def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError:
            raise GitHubError("GitHub returned invalid JSON.") from None

    async def account(self, login: str | None = None) -> AccountSnapshot:
        raw = JSON_OBJECT.validate_python(
            self._json(
                await self._request(
                    "GET", f"/users/{quote(login, safe='')}" if login else "/user"
                )
            )
        )
        account = AccountSnapshot.model_validate(
            {
                "id": str(raw.get("id") or ""),
                "login": raw.get("login"),
                "kind": "Organization" if raw.get("type") == "Organization" else "User",
            }
        )
        if login is None:
            self.authenticated = account
        return account

    async def repository(self, name: str) -> RepositorySnapshot:
        response = await self._request("GET", f"/repos/{quote(name, safe='/')}")
        return RepositorySnapshot.from_github(
            JSON_OBJECT.validate_python(self._json(response))
        )

    async def _pages(
        self, path: str, params: dict[str, str | int] | None
    ) -> AsyncIterator[list[JsonObject]]:
        url = API_URL.join(path)
        seen: set[httpx.URL] = set()
        while True:
            response = await self._request("GET", url, params=params)
            if response.url in seen:
                raise GitHubError("GitHub returned a repeated pagination URL.")
            seen.add(response.url)
            yield JSON_OBJECTS.validate_python(self._json(response))
            link = response.links.get("next", {}).get("url")
            if not link:
                return
            url = self._safe_url(response.url.join(link))
            params = None

    async def repositories(
        self, owner: AccountSnapshot | None = None
    ) -> AsyncIterator[list[RepositorySnapshot]]:
        params: dict[str, str | int] = {"sort": "full_name", "per_page": 100}
        if owner is None or (self.authenticated and owner.id == self.authenticated.id):
            path = "/user/repos"
            params["affiliation"] = "owner"
        elif owner.kind == "Organization":
            path = f"/orgs/{quote(owner.login, safe='')}/repos"
            params["type"] = "all"
        else:
            path = f"/users/{quote(owner.login, safe='')}/repos"
            params["type"] = "owner"
        async for batch in self._pages(path, params):
            yield [RepositorySnapshot.from_github(raw) for raw in batch]

    async def issues(
        self, repo: RepositorySnapshot, since: datetime | None
    ) -> AsyncIterator[list[ItemSnapshot]]:
        params: dict[str, str | int] = {
            "state": "all",
            "sort": "updated",
            "direction": "asc",
            "per_page": 100,
        }
        if since is not None:
            params["since"] = since.isoformat()
        async for batch in self._pages(
            f"/repos/{quote(repo.name, safe='/')}/issues", params
        ):
            yield [ItemSnapshot.from_github(raw, repo.id) for raw in batch]

    async def pull(self, repo: RepositorySnapshot, number: int) -> JsonObject:
        response = await self._request(
            "GET", f"/repos/{quote(repo.name, safe='/')}/pulls/{number}"
        )
        raw = JSON_OBJECT.validate_python(self._json(response))
        PullPayload.model_validate(raw)
        return raw

    async def issue(self, repo: RepositorySnapshot, number: int) -> ItemSnapshot:
        response = await self._request(
            "GET", f"/repos/{quote(repo.name, safe='/')}/issues/{number}"
        )
        return ItemSnapshot.from_github(
            JSON_OBJECT.validate_python(self._json(response)), repo.id
        )

    async def comments(
        self, repo: RepositorySnapshot, since: datetime | None
    ) -> AsyncIterator[list[CommentSnapshot]]:
        params: dict[str, str | int] = {
            "sort": "updated",
            "direction": "asc",
            "per_page": 100,
        }
        if since is not None:
            params["since"] = since.isoformat()
        for kind, endpoint, parent_key in (
            ("issue", "issues/comments", "issue_url"),
            ("inline", "pulls/comments", "pull_request_url"),
        ):
            async for batch in self._pages(
                f"/repos/{quote(repo.name, safe='/')}/{endpoint}", params
            ):
                comments = []
                for raw in batch:
                    parent = raw.get(parent_key)
                    if not isinstance(parent, str):
                        raise ValueError(
                            "GitHub comment is missing its parent item URL."
                        )
                    comments.append(
                        CommentSnapshot.from_github(
                            raw,
                            repo.id,
                            int(parent.rsplit("/", 1)[-1]),
                            "issue" if kind == "issue" else "inline",
                        )
                    )
                yield comments

    async def reviews(
        self, repo: RepositorySnapshot, number: int
    ) -> AsyncIterator[list[CommentSnapshot]]:
        async for batch in self._pages(
            f"/repos/{quote(repo.name, safe='/')}/pulls/{number}/reviews",
            {"per_page": 100},
        ):
            yield [
                CommentSnapshot.from_github(raw, repo.id, number, "review")
                for raw in batch
            ]

    async def repository_activity(
        self, repos: list[RepositorySnapshot]
    ) -> dict[str, RepositoryActivity]:
        result = {}
        for offset in range(0, len(repos), SIGNAL_BATCH_SIZE):
            batch = repos[offset : offset + SIGNAL_BATCH_SIZE]
            variables: JsonObject = {}
            declarations: list[str] = []
            selections: list[str] = []
            for index, repo in enumerate(batch):
                owner, name = repo.name.split("/", 1)
                variables[f"owner{index}"] = owner
                variables[f"name{index}"] = name
                declarations.extend((f"$owner{index}:String!", f"$name{index}:String!"))
                selections.append(
                    f"r{index}:repository(owner:$owner{index},name:$name{index}){{...Activity}}"
                )
            query = (
                "query RepositoryActivity("
                + ",".join(declarations)
                + "){"
                + " ".join(selections)
                + "}"
                + """
              fragment Activity on Repository {
                databaseId
                defaultBranchRef {
                  name target {... on Commit {oid messageHeadline committedDate url}}
                }
                latestRelease {id name tagName publishedAt url}
                pullRequests(first:1,states:[OPEN,CLOSED,MERGED],
                  orderBy:{field:CREATED_AT,direction:DESC}) {
                  nodes {id number title state createdAt updatedAt url}
                }
                issues(first:1,states:[OPEN,CLOSED],
                  orderBy:{field:CREATED_AT,direction:DESC}) {
                  nodes {id number title state createdAt updatedAt url}
                }
              }
            """
            )
            payload = JSON_OBJECT.validate_python(
                self._json(
                    await self._request(
                        "POST",
                        "/graphql",
                        body={"query": query, "variables": variables},
                    )
                )
            )
            data = payload.get("data")
            errors = payload.get("errors", [])
            if not isinstance(data, dict) or not isinstance(errors, list):
                raise GitHubError("GitHub returned incomplete repository activity.")
            failed = set()
            for error in errors:
                path = error.get("path") if isinstance(error, dict) else None
                if (
                    not isinstance(path, list)
                    or not path
                    or not isinstance(path[0], str)
                    or path[0] not in {f"r{i}" for i in range(len(batch))}
                ):
                    raise GitHubError("GitHub returned incomplete repository activity.")
                failed.add(str(path[0]))
            for index, repo in enumerate(batch):
                alias = f"r{index}"
                raw = data.get(alias)
                if alias in failed or not isinstance(raw, dict):
                    continue
                try:
                    result[repo.id] = RepositoryActivity.from_github(raw, repo.id)
                except (ValueError, KeyError, TypeError):
                    # Other aliases can be complete even when one repository is
                    # inaccessible or one nested field is missing/malformed.
                    continue
        return result

    async def signals(self, ids: list[str]) -> dict[str, JsonObject]:
        result: dict[str, JsonObject] = {}
        batch_size = SIGNAL_BATCH_SIZE
        for offset in range(0, len(ids), batch_size):
            batch = ids[offset : offset + batch_size]
            response = JSON_OBJECT.validate_python(
                self._json(
                    await self._request(
                        "POST",
                        "/graphql",
                        body={
                            "query": """query($ids:[ID!]!){nodes(ids:$ids){
                            ... on PullRequest{
                              id number reviewDecision mergeable mergeStateStatus
                              headRefOid statusCheckRollup{state}
                            }}}""",
                            "variables": {"ids": list(batch)},
                        },
                    )
                )
            )
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
                # Preserve the public stored signal shape while querying the head
                # directly, without traversing a commit connection per PR.
                if "headRefOid" in raw or "statusCheckRollup" in raw:
                    head = Commit.model_validate(
                        {
                            "oid": raw.pop("headRefOid", None),
                            "statusCheckRollup": raw.pop("statusCheckRollup", None),
                        }
                    )
                    raw["commits"] = {
                        "nodes": [{"commit": head.model_dump(mode="json")}]
                    }
                signal = SignalsPayload.model_validate(raw)
                if not signal.id or signal.id not in batch:
                    raise GitHubError(
                        "GitHub returned an unexpected pull request signal."
                    )
                result[signal.id] = raw
            if not set(batch) <= result.keys():
                raise GitHubError("GitHub returned incomplete pull request signals.")
        return result
