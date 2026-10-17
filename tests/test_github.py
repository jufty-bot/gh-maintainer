"""Validate the SDK adapter through actual PyGithub request/response handling."""

import asyncio
import threading
import time

import pytest
import requests_mock
from github import Auth, Github
from urllib3.response import HTTPResponse

from gh_maintainer.clock import parse_date
from gh_maintainer.github import BoundedGitHubRetry, GitHubError, PyGithubSource
from gh_maintainer.schemas import RepositorySnapshot

from .fakes import AT, REPO, issue

pytestmark = pytest.mark.anyio


def source() -> PyGithubSource:
    # SDK retries are configured separately; HTTP mocks avoid sleeping in tests.
    sdk = Github(
        auth=Auth.Token("secret"),
        per_page=100,
        lazy=True,
        retry=None,
        seconds_between_requests=0,
    )
    return PyGithubSource("secret", sdk=sdk)


async def test_sdk_pagination_beyond_search_limit() -> None:
    adapter = source()
    try:
        with requests_mock.Mocker() as mock:
            for page in range(1, 12):
                headers = (
                    {
                        "Link": "<https://api.github.com/repos/owner/project/issues"
                        f'?page={page + 1}&per_page=100>; rel="next"'
                    }
                    if page < 11
                    else {}
                )
                url = "https://api.github.com:443/repos/owner/project/issues"
                if page > 1:
                    url += f"?page={page}&per_page=100"
                mock.get(
                    url,
                    json=[
                        issue(number)
                        for number in range(
                            (page - 1) * 100 + 1, min(page * 100 + 1, 1002)
                        )
                    ],
                    headers=headers,
                )
            repo = RepositorySnapshot.from_github(REPO)
            items = [
                item async for batch in adapter.issues(repo, None) for item in batch
            ]
            assert len(items) == 1001
            assert len(mock.request_history) == 11
    finally:
        await adapter.close()


async def test_sdk_validates_graphql_and_uses_issue_and_pr_ids_separately() -> None:
    adapter = source()
    try:
        with requests_mock.Mocker() as mock:
            mock.post(
                "https://api.github.com:443/graphql",
                json={"data": {"nodes": [{"id": "PR_2", "commits": {"nodes": []}}]}},
            )
            assert (await adapter.signals(["PR_2"]))["PR_2"]["id"] == "PR_2"
            assert mock.last_request.json()["variables"]["ids"] == ["PR_2"]
            mock.post(
                "https://api.github.com:443/graphql",
                json={"data": None, "errors": [{"message": "denied"}]},
            )
            with pytest.raises(GitHubError):
                await adapter.signals(["PR_2"])
            mock.post(
                "https://api.github.com:443/graphql", json={"data": {"nodes": [None]}}
            )
            with pytest.raises(ValueError):
                await adapter.signals(["PR_2"])
    finally:
        await adapter.close()


async def test_sdk_errors_do_not_expose_response_bodies_or_credentials() -> None:
    adapter = source()
    try:
        with requests_mock.Mocker() as mock:
            mock.get(
                "https://api.github.com:443/user",
                status_code=403,
                json={"message": "secret private body"},
            )
            with pytest.raises(GitHubError) as error:
                await adapter.account()
            assert "secret" not in str(error.value)
            assert "403" in str(error.value)
    finally:
        await adapter.close()


async def test_lazy_sdk_iteration_stays_off_event_loop() -> None:
    adapter = source()
    main_thread = threading.get_ident()
    threads = []
    ticked = asyncio.Event()
    try:
        with requests_mock.Mocker() as mock:

            def response(_request, _context):
                threads.append(threading.get_ident())
                time.sleep(0.03)
                return [REPO]

            mock.get("https://api.github.com:443/user/repos", json=response)

            async def tick() -> None:
                await asyncio.sleep(0.005)
                ticked.set()

            task = asyncio.create_task(tick())
            repos = [repo async for batch in adapter.repositories() for repo in batch]
            assert ticked.is_set()
            await task
            assert len(repos) == 1
            assert all(thread != main_thread for thread in threads)
    finally:
        await adapter.close()


async def test_sdk_rejects_foreign_pagination_links() -> None:
    adapter = source()
    try:
        with requests_mock.Mocker() as mock:
            mock.get(
                "https://api.github.com:443/user/repos",
                json=[REPO],
                headers={"Link": '<https://evil.example/repos>; rel="next"'},
            )
            with pytest.raises(GitHubError, match="rejected"):
                _ = [repo async for batch in adapter.repositories() for repo in batch]
            assert len(mock.request_history) == 1
    finally:
        await adapter.close()


async def test_retry_after_wait_is_bounded_without_ignoring_server_delay() -> None:
    retry = BoundedGitHubRetry(total=3, max_rate_limit_wait=30)
    assert retry.get_retry_after(HTTPResponse(headers={"Retry-After": "15"})) == 15
    # urllib3 clones Retry instances for each attempt; retain the bounded policy.
    with pytest.raises(GitHubError, match="longer than 30"):
        retry.new().get_retry_after(HTTPResponse(headers={"Retry-After": "120"}))


@pytest.mark.parametrize("mode", ["default", "self", "organization", "public-user"])
async def test_sdk_owner_inventory_routes_and_pagination(mode: str) -> None:
    adapter = source()
    try:
        with requests_mock.Mocker() as mock:
            login = "owner" if mode in {"default", "self"} else "team"
            kind = "Organization" if mode == "organization" else "User"
            account_data = {
                "id": 77,
                "login": login,
                "type": kind,
                "url": f"https://api.github.com/users/{login}",
            }
            mock.get("https://api.github.com:443/user", json=account_data)
            mock.get(f"https://api.github.com:443/users/{login}", json=account_data)
            if mode in {"default", "self"}:
                await adapter.account()
                endpoint, parameter = "/user/repos", "affiliation=owner"
            elif mode == "organization":
                endpoint, parameter = "/orgs/team/repos", "type=all"
            else:
                endpoint, parameter = "/users/team/repos", "type=owner"
            owner = None if mode == "default" else await adapter.account(login)
            mock.get(
                f"https://api.github.com:443{endpoint}",
                json=[REPO],
                headers={
                    "Link": f'<https://api.github.com{endpoint}?page=2>; rel="next"'
                },
            )
            mock.get(
                f"https://api.github.com:443{endpoint}?page=2",
                json=[{**REPO, "id": 2}],
            )
            repos = [
                repo async for batch in adapter.repositories(owner) for repo in batch
            ]
            assert [repo.id for repo in repos] == ["1", "2"]
            requests = [
                request for request in mock.request_history if request.path == endpoint
            ]
            assert len(requests) == 2
            assert parameter in requests[0].query
            assert "sort=full_name" in requests[0].query
    finally:
        await adapter.close()


async def test_sdk_explicit_repository_uses_direct_endpoint() -> None:
    adapter = source()
    try:
        with requests_mock.Mocker() as mock:
            mock.get(
                "https://api.github.com:443/repos/team/another",
                json={**REPO, "full_name": "team/another"},
            )
            repo = await adapter.repository("team/another")
            assert repo.id == "1" and repo.name == "team/another"
            assert len(mock.request_history) == 1
    finally:
        await adapter.close()


async def test_sdk_conversations_pagination_incremental_and_review_bodies() -> None:
    adapter = source()
    raw = {
        "node_id": "IC_1",
        "body": "Context",
        "user": None,
        "html_url": "https://github.com/owner/project/issues/2#issuecomment-1",
        "created_at": AT,
        "updated_at": AT,
        "issue_url": "https://api.github.com/repos/owner/project/issues/2",
    }
    try:
        with requests_mock.Mocker() as mock:
            mock.get(
                "https://api.github.com:443/repos/owner/project/issues/comments",
                json=[{**raw, "node_id": f"IC_{number}"} for number in range(100)],
                headers={
                    "Link": "<https://api.github.com/repos/owner/project/issues/"
                    'comments?page=2>; rel="next"'
                },
            )
            mock.get(
                "https://api.github.com:443/repos/owner/project/issues/comments?page=2",
                json=[{**raw, "node_id": "IC_100"}],
            )
            mock.get(
                "https://api.github.com:443/repos/owner/project/pulls/comments",
                json=[
                    {
                        **raw,
                        "node_id": "RC_1",
                        "pull_request_url": "https://api.github.com/repos/owner/project/pulls/2",
                        "path": "example.py",
                        "in_reply_to_id": 4,
                    }
                ],
            )
            repo = RepositorySnapshot.from_github(REPO)
            comments = [
                comment
                async for batch in adapter.comments(repo, parse_date(AT))
                for comment in batch
            ]
            assert len(comments) == 102
            assert (
                comments[-1].kind == "inline"
                and comments[-1].raw["in_reply_to_id"] == 4
            )
            assert comments[0].author is None
            assert mock.request_history[0].qs["sort"] == ["updated"]
            assert "since" in mock.request_history[0].qs
            review = {
                **raw,
                "node_id": "RV_1",
                "submitted_at": AT,
                "state": "APPROVED",
                "body": "Looks good",
            }
            review.pop("created_at")
            review.pop("updated_at")
            mock.get(
                "https://api.github.com:443/repos/owner/project/pulls/2/reviews",
                json=[review],
            )
            reviews = [
                review async for batch in adapter.reviews(repo, 2) for review in batch
            ]
            assert reviews[0].body == "Looks good" and reviews[0].kind == "review"
            assert reviews[0].updated_at == parse_date(AT)
            assert len(mock.request_history) == 4
    finally:
        await adapter.close()


async def test_sdk_comment_with_missing_parent_fails_coverage() -> None:
    adapter = source()
    try:
        with requests_mock.Mocker() as mock:
            mock.get(
                "https://api.github.com:443/repos/owner/project/issues/comments",
                json=[{"node_id": "broken"}],
            )
            with pytest.raises(ValueError, match="parent"):
                _ = [
                    batch
                    async for batch in adapter.comments(
                        RepositorySnapshot.from_github(REPO), None
                    )
                ]
    finally:
        await adapter.close()


async def test_sdk_fetches_missing_comment_parent_as_issue_identity() -> None:
    adapter = source()
    try:
        with requests_mock.Mocker() as mock:
            mock.get(
                "https://api.github.com:443/repos/owner/project/issues/8",
                json=issue(8, True),
            )
            parent = await adapter.issue(RepositorySnapshot.from_github(REPO), 8)
            assert parent.id == "I_8" and parent.kind.value == "pr"
    finally:
        await adapter.close()
