"""Validate the SDK adapter through actual PyGithub request/response handling."""

import asyncio
import threading
import time

import pytest
import requests_mock
from github import Auth, Github
from urllib3.response import HTTPResponse

from gh_maintainer.github import BoundedGitHubRetry, GitHubError, PyGithubSource
from gh_maintainer.schemas import RepositorySnapshot

from .fakes import REPO, issue

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
