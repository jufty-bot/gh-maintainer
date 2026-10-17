"""Exercise async HTTP, pagination, bounded fanout, retries and source validation."""

import asyncio
import json
import time
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from gh_maintainer.clock import parse_date
from gh_maintainer.github import AsyncGitHubSource, GitHubError
from gh_maintainer.repositories import CatalogRepository
from gh_maintainer.schemas import RepositorySnapshot, State
from gh_maintainer.sync import SyncService

from .fakes import AT, REPO, FakeSource, issue

pytestmark = pytest.mark.anyio


def source(**kwargs) -> AsyncGitHubSource:
    # Contract tests isolate HTTP semantics; pacing has dedicated tests below.
    kwargs.setdefault("requests_per_second", 1_000_000)
    return AsyncGitHubSource("secret", **kwargs)


async def test_pagination_beyond_search_limit() -> None:
    requests = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params.get("page", "1"))
        headers = (
            {
                "Link": "<https://api.github.com/repos/owner/project/issues"
                f'?page={page + 1}&per_page=100>; rel="next"'
            }
            if page < 11
            else {}
        )
        return httpx.Response(
            200,
            json=[
                issue(number)
                for number in range((page - 1) * 100 + 1, min(page * 100 + 1, 1002))
            ],
            headers=headers,
        )

    adapter = source(transport=httpx.MockTransport(respond))
    try:
        repo = RepositorySnapshot.from_github(REPO)
        since = parse_date("2026-10-01T00:00:00Z")
        items = [item async for batch in adapter.issues(repo, since) for item in batch]
        assert len(items) == 1001
        assert adapter.calls == len(requests) == 11
        assert requests[0].url.params["state"] == "all"
        assert requests[0].url.params["since"] == since.isoformat()
        assert requests[-1].url.params["page"] == "11"
        assert requests[0].headers["authorization"] == "Bearer secret"
    finally:
        await adapter.close()
    assert adapter.client.is_closed


async def test_graphql_batches_and_separates_issue_and_pr_ids() -> None:
    batches = []

    def respond(request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["variables"]["ids"]
        batches.append(ids)
        return httpx.Response(
            200, json={"data": {"nodes": [{"id": node_id} for node_id in ids]}}
        )

    adapter = source(transport=httpx.MockTransport(respond))
    try:
        ids = [f"PR_{number}" for number in range(45)]
        assert set(await adapter.signals(ids)) == set(ids)
        assert [len(batch) for batch in batches] == [20, 20, 5]
        assert await adapter.signals([]) == {}
        assert adapter.calls == 3
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"data": None, "errors": [{"message": "denied"}]},
        {"data": {"nodes": [None]}},
        {"data": {"nodes": []}},
        {"data": {"nodes": [{"id": "OTHER"}]}},
        {"data": {"nodes": [{"id": "PR_2"}, {"id": "PR_2"}]}},
    ],
)
async def test_incomplete_graphql_is_rejected(payload: dict) -> None:
    adapter = source(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    try:
        with pytest.raises((GitHubError, ValueError)):
            await adapter.signals(["PR_2", "PR_3"])
    finally:
        await adapter.close()


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_errors_do_not_expose_response_bodies_or_credentials(status: int) -> None:
    adapter = source(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json={"message": "secret private body"})
        ),
    )
    try:
        with pytest.raises(GitHubError) as error:
            await adapter.account()
        assert "secret" not in str(error.value)
        assert str(status) in str(error.value)
        assert adapter.calls == 1
    finally:
        await adapter.close()


async def test_async_requests_overlap_with_a_global_limit() -> None:
    active = peak = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            entered.set()
        try:
            await release.wait()
            return httpx.Response(200, json={"id": 77, "login": "owner"})
        finally:
            active -= 1

    adapter = source(concurrency=2, transport=httpx.MockTransport(respond))
    tasks = [asyncio.create_task(adapter.account()) for _ in range(6)]
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert peak == 2
        assert adapter.calls == 2
        release.set()
        assert len(await asyncio.gather(*tasks)) == 6
        assert peak == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await adapter.close()


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example/repos",
        "http://api.github.com/repos",
        "https://api.github.com:444/repos",
        "https://secret@api.github.com/repos",
    ],
)
async def test_rejects_unsafe_pagination_links(link: str) -> None:
    adapter = source(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json=[REPO], headers={"Link": f'<{link}>; rel="next"'}
            )
        ),
    )
    try:
        with pytest.raises(GitHubError, match="unsafe"):
            _ = [repo async for batch in adapter.repositories() for repo in batch]
        assert adapter.calls == 1
    finally:
        await adapter.close()


async def test_rejects_repeated_pagination_links() -> None:
    adapter = source(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=[REPO], headers={"Link": f'<{request.url}>; rel="next"'}
            )
        ),
    )
    try:
        with pytest.raises(GitHubError, match="repeated"):
            _ = [repo async for batch in adapter.repositories() for repo in batch]
        assert adapter.calls == 2
    finally:
        await adapter.close()


@pytest.mark.parametrize("foreign", [False, True])
async def test_redirects_are_bounded_and_keep_credentials_on_github(
    foreign: bool,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(
                301,
                headers={
                    "Location": "https://evil.example/user" if foreign else "/renamed"
                },
            )
        return httpx.Response(200, json={"id": 77, "login": "owner"})

    adapter = source(transport=httpx.MockTransport(respond))
    try:
        if foreign:
            with pytest.raises(GitHubError, match="unsafe"):
                await adapter.account()
            assert adapter.calls == 1
        else:
            assert (await adapter.account()).login == "owner"
            assert adapter.calls == 2
    finally:
        await adapter.close()


@pytest.mark.parametrize("network", [False, True])
async def test_retries_are_bounded_and_async(monkeypatch, network: bool) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("gh_maintainer.github.asyncio.sleep", sleep)

    def respond(request: httpx.Request) -> httpx.Response:
        if network:
            raise httpx.ConnectError("secret", request=request)
        return httpx.Response(503, json={"message": "secret"})

    adapter = source(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(GitHubError) as error:
            await adapter.account()
        assert "secret" not in str(error.value)
        assert adapter.calls == 4
        assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4]
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "headers",
    [
        {"Retry-After": "120"},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(time.time() + 120)},
    ],
)
async def test_long_rate_limits_stop_all_workers_without_sleeping(
    headers: dict,
) -> None:
    adapter = source(
        transport=httpx.MockTransport(lambda _: httpx.Response(429, headers=headers)),
    )
    try:
        for _ in range(2):
            with pytest.raises(GitHubError, match="longer than 30"):
                await adapter.account()
        assert adapter.calls == 1
    finally:
        await adapter.close()


async def test_short_rate_limit_is_retried(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("gh_maintainer.github.asyncio.sleep", sleep)
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"id": 77, "login": "owner"}),
        ]
    )
    adapter = source(transport=httpx.MockTransport(lambda _: next(responses)))
    try:
        assert (await adapter.account()).id == "77"
        assert adapter.calls == 2
        sleep.assert_awaited_once_with(1)
    finally:
        await adapter.close()


async def test_cancellation_releases_request_slot() -> None:
    entered = asyncio.Event()

    async def respond(_request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        return httpx.Response(200)

    adapter = source(concurrency=1, transport=httpx.MockTransport(respond))
    task = asyncio.create_task(adapter.account())
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(adapter.slots.acquire(), timeout=2)
        adapter.slots.release()
    finally:
        await adapter.close()


async def test_http_source_syncs_incrementally(database) -> None:
    fake = FakeSource()
    repo = RepositorySnapshot.from_github(REPO)

    async def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user":
            payload = {"id": 77, "login": "owner"}
        elif path == "/user/repos":
            payload = fake.inventory
        elif path.endswith("/issues"):
            payload = fake.items
        elif path.endswith("/comments") or path.endswith("/reviews"):
            payload = []
        elif "/pulls/" in path:
            payload = await fake.pull(repo, int(path.rsplit("/", 1)[-1]))
        elif path == "/graphql":
            ids = json.loads(request.content)["variables"]["ids"]
            payload = {"data": {"nodes": list((await fake.signals(ids)).values())}}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=payload)

    adapter = source(transport=httpx.MockTransport(respond))
    try:
        assert (await SyncService(database, adapter).run()).source_calls == 10
        adapter.calls = 0
        result = await SyncService(database, adapter).run()
        assert result.status == "complete"
        assert result.source_calls == 9
        items = await CatalogRepository(database).items("1")
        assert next(item for item in items if item.number == 3).state == State.MERGED
    finally:
        await adapter.close()


async def test_secondary_limit_without_headers_stops_other_requests() -> None:
    adapter = source(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                403, json={"message": "You have exceeded a secondary rate limit."}
            )
        ),
    )
    try:
        for _ in range(2):
            with pytest.raises(GitHubError, match="longer than 30"):
                await adapter.account()
        assert adapter.calls == 1
    finally:
        await adapter.close()


async def test_shared_limiter_spaces_requests_without_bursting() -> None:
    started = []

    async def respond(_request: httpx.Request) -> httpx.Response:
        started.append(asyncio.get_running_loop().time())
        return httpx.Response(200, json={"id": 77, "login": "owner"})

    adapter = source(requests_per_second=50, transport=httpx.MockTransport(respond))
    ticked = asyncio.Event()

    async def tick() -> None:
        await asyncio.sleep(0.005)
        ticked.set()

    try:
        ticker = asyncio.create_task(tick())
        await asyncio.gather(*(adapter.account() for _ in range(4)))
        await ticker
        assert ticked.is_set()
        assert len(started) == 4
        assert all(later - earlier >= 0.019 for earlier, later in pairwise(started))
    finally:
        await adapter.close()


async def test_retry_and_redirect_attempts_all_acquire_a_permit(monkeypatch) -> None:
    monkeypatch.setattr("gh_maintainer.github.asyncio.sleep", AsyncMock())
    acquire = AsyncMock()
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(301, headers={"Location": "/renamed"}),
            httpx.Response(200, json={"id": 77, "login": "owner"}),
        ]
    )
    adapter = source(transport=httpx.MockTransport(lambda _: next(responses)))
    adapter.pacer = SimpleNamespace(acquire=acquire)
    try:
        assert (await adapter.account()).login == "owner"
        assert adapter.calls == acquire.await_count == 3
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "error",
    [
        {"type": "RATE_LIMITED"},
        {"extensions": {"code": "RATE_LIMITED"}},
        {"message": "API rate limit exceeded for secret"},
    ],
)
async def test_graphql_200_rate_limits_pause_all_workers(error: dict) -> None:
    adapter = source(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"data": {"nodes": [{"id": "PR_2"}]}, "errors": [error]}
            )
        )
    )
    try:
        with pytest.raises(GitHubError, match="longer than 30") as failure:
            await adapter.signals(["PR_2"])
        assert "secret" not in str(failure.value)
        with pytest.raises(GitHubError, match="longer than 30"):
            await adapter.account()
        assert adapter.calls == 1
    finally:
        await adapter.close()


async def test_graphql_permission_errors_do_not_trigger_a_cooldown() -> None:
    responses = iter(
        [
            httpx.Response(
                200, json={"errors": [{"message": "Resource not accessible"}]}
            ),
            httpx.Response(200, json={"id": 77, "login": "owner"}),
        ]
    )
    adapter = source(transport=httpx.MockTransport(lambda _: next(responses)))
    try:
        with pytest.raises(GitHubError, match="incomplete"):
            await adapter.signals(["PR_2"])
        assert (await adapter.account()).login == "owner"
        assert adapter.calls == 2
    finally:
        await adapter.close()


async def test_cooldown_is_rechecked_after_waiting_for_a_permit() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def acquire() -> None:
        entered.set()
        await release.wait()

    adapter = source(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"id": 77, "login": "owner"})
        )
    )
    adapter.pacer = SimpleNamespace(acquire=acquire)
    task = asyncio.create_task(adapter.account())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        adapter.resume_at = time.monotonic() + 120
        release.set()
        with pytest.raises(GitHubError, match="longer than 30"):
            await task
        assert adapter.calls == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await adapter.close()


async def test_cancellation_while_pacing_releases_admission_and_request_slots() -> None:
    adapter = source(
        concurrency=1,
        requests_per_second=0.001,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"id": 77, "login": "owner"})
        ),
    )
    try:
        await adapter.account()
        task = asyncio.create_task(adapter.account())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not adapter.admission_lock.locked()
        await asyncio.wait_for(adapter.slots.acquire(), timeout=2)
        adapter.slots.release()
        assert adapter.calls == 1
    finally:
        await adapter.close()


async def test_low_budget_pacing_and_resource_isolation(monkeypatch) -> None:
    clock = SimpleNamespace(wall=1_000.0, monotonic=100.0)
    monkeypatch.setattr(
        "gh_maintainer.github.time",
        SimpleNamespace(time=lambda: clock.wall, monotonic=lambda: clock.monotonic),
    )
    sleeps = []

    async def advance(delay: float) -> None:
        sleeps.append(delay)
        clock.wall += delay
        clock.monotonic += delay

    monkeypatch.setattr("gh_maintainer.github.asyncio.sleep", advance)
    adapter = source()
    adapter.pacer = SimpleNamespace(acquire=AsyncMock())
    headers = {
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": "10",
        "X-RateLimit-Reset": "1100",
    }
    try:
        adapter._observe_budget(httpx.Response(200, headers=headers), "core")
        await adapter._admit("core")
        assert sleeps == [10.0]
        assert adapter.budgets["core"].next_at == 119.0
        await adapter._admit("graphql")
        assert sleeps == [10.0]
        # A stale concurrent response cannot restore capacity or skip pacing.
        adapter._observe_budget(
            httpx.Response(
                200,
                headers={
                    **headers,
                    "X-RateLimit-Remaining": "20",
                },
            ),
            "core",
        )
        assert adapter.budgets["core"].remaining == 10
        assert adapter.budgets["core"].next_at == 119.0
        adapter._observe_budget(
            httpx.Response(
                200,
                headers={
                    **headers,
                    "X-RateLimit-Reset": "1050",
                },
            ),
            "core",
        )
        assert adapter.budgets["core"].reset_at == 1100
        # A new window restores the configured rate.
        adapter._observe_budget(
            httpx.Response(
                200,
                headers={
                    **headers,
                    "X-RateLimit-Reset": "1200",
                    "X-RateLimit-Remaining": "100",
                },
            ),
            "core",
        )
        await adapter._admit("core")
        assert sleeps == [10.0]
    finally:
        await adapter.close()


@pytest.mark.parametrize("rate", [0, -1, float("inf"), float("nan")])
async def test_request_rate_must_be_positive_and_finite(rate: float) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        source(requests_per_second=rate)


async def test_queued_workers_do_not_burst_after_shared_cooldown(monkeypatch) -> None:
    original_sleep = asyncio.sleep

    async def sleep(delay: float) -> None:
        # Keep the real short cooldown and pacing; skip exponential retry delays.
        await original_sleep(delay if delay < 1 else 0)

    monkeypatch.setattr("gh_maintainer.github.asyncio.sleep", sleep)
    started = []

    def respond(_request: httpx.Request) -> httpx.Response:
        started.append(asyncio.get_running_loop().time())
        if len(started) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.04"})
        return httpx.Response(200, json={"id": 77, "login": "owner"})

    adapter = source(requests_per_second=50, transport=httpx.MockTransport(respond))
    try:
        results = await asyncio.gather(*(adapter.account() for _ in range(4)))
        assert len(results) == 4
        assert len(started) == 5
        assert started[1] - started[0] >= 0.039
        assert all(later - earlier >= 0.019 for earlier, later in pairwise(started))
    finally:
        await adapter.close()


async def test_successful_last_budgeted_response_is_retained() -> None:
    adapter = source(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"id": 77, "login": "owner"},
                headers={
                    "X-RateLimit-Limit": "100",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(time.time() + 120),
                },
            )
        )
    )
    try:
        assert (await adapter.account()).login == "owner"
        with pytest.raises(GitHubError, match="longer than 30"):
            await adapter.account()
        assert adapter.calls == 1
    finally:
        await adapter.close()


async def test_graphql_200_rate_limit_retries_are_bounded(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("gh_maintainer.github.asyncio.sleep", sleep)
    adapter = source(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"Retry-After": "0"},
                json={"errors": [{"type": "RATE_LIMITED"}]},
            )
        )
    )
    try:
        with pytest.raises(GitHubError, match="rate limit persisted"):
            await adapter.signals(["PR_2"])
        assert adapter.calls == 4
        assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4]
    finally:
        await adapter.close()


@pytest.mark.parametrize("mode", ["default", "self", "organization", "public-user"])
async def test_owner_inventory_routes_and_pagination(mode: str) -> None:
    requests = []
    login = "owner" if mode in {"default", "self"} else "team"
    kind = "Organization" if mode == "organization" else "User"
    endpoint = (
        "/user/repos"
        if mode in {"default", "self"}
        else "/orgs/team/repos"
        if mode == "organization"
        else "/users/team/repos"
    )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if not request.url.path.endswith("/repos"):
            return httpx.Response(200, json={"id": 77, "login": login, "type": kind})
        page = request.url.params.get("page")
        return httpx.Response(
            200,
            json=[{**REPO, "id": 2 if page else 1}],
            headers={}
            if page
            else {"Link": f'<https://api.github.com{endpoint}?page=2>; rel="next"'},
        )

    adapter = source(transport=httpx.MockTransport(respond))
    try:
        if mode in {"default", "self"}:
            await adapter.account()
        owner = None if mode == "default" else await adapter.account(login)
        repos = [repo async for batch in adapter.repositories(owner) for repo in batch]
        assert [repo.id for repo in repos] == ["1", "2"]
        listing = [request for request in requests if request.url.path == endpoint]
        assert len(listing) == 2
        assert listing[0].url.params["sort"] == "full_name"
        assert listing[0].url.params[
            "affiliation" if mode in {"default", "self"} else "type"
        ] == ("owner" if mode != "organization" else "all")
    finally:
        await adapter.close()


async def test_direct_repository_and_missing_comment_parent() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json=issue(8, True) if request.url.path.endswith("/8") else REPO
        )

    adapter = source(transport=httpx.MockTransport(respond))
    try:
        repo = await adapter.repository("owner/project")
        parent = await adapter.issue(repo, 8)
        assert parent.id == "I_8" and parent.kind.value == "pr"
        assert [request.url.path for request in requests] == [
            "/repos/owner/project",
            "/repos/owner/project/issues/8",
        ]
    finally:
        await adapter.close()


async def test_conversations_pagination_incremental_and_review_bodies() -> None:
    requests = []
    raw = {
        "node_id": "IC_1",
        "body": "Context",
        "user": None,
        "html_url": "https://github.com/owner/project/issues/2#issuecomment-1",
        "created_at": AT,
        "updated_at": AT,
        "issue_url": "https://api.github.com/repos/owner/project/issues/2",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/issues/comments"):
            if request.url.params.get("page"):
                return httpx.Response(200, json=[{**raw, "node_id": "IC_100"}])
            return httpx.Response(
                200,
                json=[{**raw, "node_id": f"IC_{n}"} for n in range(100)],
                headers={
                    "Link": "<https://api.github.com/repos/owner/project/issues/"
                    'comments?page=2>; rel="next"'
                },
            )
        if path.endswith("/pulls/comments"):
            return httpx.Response(
                200,
                json=[
                    {
                        **raw,
                        "node_id": "RC_1",
                        "path": "example.py",
                        "in_reply_to_id": 4,
                        "pull_request_url": "https://api.github.com/repos/owner/project/pulls/2",
                    }
                ],
            )
        review = {
            **raw,
            "node_id": "RV_1",
            "submitted_at": AT,
            "state": "APPROVED",
            "body": "Looks good",
        }
        review.pop("created_at")
        review.pop("updated_at")
        return httpx.Response(200, json=[review])

    adapter = source(transport=httpx.MockTransport(respond))
    repo = RepositorySnapshot.from_github(REPO)
    try:
        comments = [
            comment
            async for batch in adapter.comments(repo, parse_date(AT))
            for comment in batch
        ]
        assert len(comments) == 102
        assert comments[-1].kind == "inline" and comments[-1].raw["in_reply_to_id"] == 4
        assert comments[0].author is None
        assert requests[0].url.params["sort"] == "updated"
        assert "since" in requests[0].url.params
        assert "since" in requests[-1].url.params
        reviews = [
            review async for batch in adapter.reviews(repo, 2) for review in batch
        ]
        assert reviews[0].body == "Looks good" and reviews[0].kind == "review"
        assert reviews[0].updated_at == parse_date(AT)
        assert len(requests) == 4
    finally:
        await adapter.close()


async def test_comment_with_missing_parent_fails_coverage() -> None:
    adapter = source(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=[{"node_id": "broken"}])
        )
    )
    try:
        with pytest.raises(ValueError, match="parent"):
            _ = [
                batch
                async for batch in adapter.comments(
                    RepositorySnapshot.from_github(REPO), None
                )
            ]
    finally:
        await adapter.close()
