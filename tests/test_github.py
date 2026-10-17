"""Exercise transport boundaries with realistic GitHub response headers."""

import httpx
import pytest

from gh_maintainer.github import GitHub, GitHubError


def test_paginate_more_than_search_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        headers = (
            {"Link": f'<https://api.github.com/items?page={page + 1}>; rel="next"'}
            if page < 11
            else {}
        )
        return httpx.Response(
            200,
            json=[{"id": i} for i in range((page - 1) * 100, min(page * 100, 1001))],
            headers=headers,
        )

    github = GitHub(
        "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert len(github.listing("/items")) == 1001
    assert github.requests == 11


def test_untrusted_pagination_never_receives_credentials() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=[], headers={"Link": '<https://evil.example/page>; rel="next"'}
        )

    github = GitHub(
        "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(GitHubError, match="another origin"):
        github.listing("/items")
    assert len(calls) == 1


def test_retry_after_and_rate_limit_budget() -> None:
    calls, waits = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, json=[])

    github = GitHub(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        wait=waits.append,
    )
    assert github.listing("/items") == []
    assert waits == [2]
    github.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(403, json={"message": "secondary rate limit"})
        )
    )
    with pytest.raises(GitHubError, match="longer pause"):
        github.request("/items")
    assert waits == [2]


def test_graphql_partial_data_is_failure() -> None:
    github = GitHub(
        "secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "data": {"nodes": [{"id": "PR_1"}]},
                        "errors": [{"message": "restricted"}],
                    },
                )
            )
        ),
    )
    with pytest.raises(GitHubError, match="incomplete"):
        github.pull_signals(["PR_1"])


def test_redirects_are_not_followed() -> None:
    github = GitHub(
        "secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    302, headers={"location": "https://evil.example"}
                )
            )
        ),
    )
    with pytest.raises(GitHubError, match="302"):
        github.request("/items")


def test_repeated_pagination_fails() -> None:
    github = GitHub(
        "secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json=[],
                    headers={"Link": '<https://api.github.com/items>; rel="next"'},
                )
            )
        ),
    )
    with pytest.raises(GitHubError, match="Repeated"):
        github.listing("/items")


def test_graphql_null_data_is_incomplete() -> None:
    github = GitHub(
        "secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json={"data": None, "errors": [{"message": "denied"}]}
                )
            )
        ),
    )
    with pytest.raises(GitHubError, match="incomplete"):
        github.pull_signals(["PR_1"])
