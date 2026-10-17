"""GitHub transport: bounded retries, trusted pagination, REST and GraphQL."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Iterator
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

Json = dict[str, Any]
RETRYABLE = {429, 500, 502, 503, 504}


class GitHubError(RuntimeError):
    """A request failed without exposing credentials or response bodies."""


def resolve_token() -> str:
    """Prefer environment credentials, otherwise use the existing GitHub CLI login."""
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token and token.strip():
        return token.strip()
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


class GitHub:
    """Serial requests avoid uncontrolled API fanout and secondary throttling."""

    def __init__(
        self,
        token: str,
        client: httpx.Client | None = None,
        base_url: str = "https://api.github.com",
        wait: Callable[[float], None] = time.sleep,
        retries: int = 3,
        max_wait: float = 30,
    ) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gh-maintainer/0.1",
        }
        self.wait, self.retries, self.max_wait = wait, retries, max_wait
        self.requests = 0
        self.quota: Json = {}
        self.before_request: Callable[[], None] | None = None

    def close(self) -> None:
        self.client.close()

    def request(self, path: str, body: Json | None = None) -> httpx.Response:
        """Never follow redirects or pagination links to another credential origin."""
        url = urljoin(self.base_url, path)
        if urlsplit(url)[:2] != urlsplit(self.base_url)[:2]:
            raise GitHubError("Refusing to send GitHub credentials to another origin.")
        for attempt in range(self.retries + 1):
            if self.before_request:
                self.before_request()
            self.requests += 1
            try:
                response = self.client.request(
                    "POST" if body is not None else "GET",
                    url,
                    headers=self.headers,
                    json=body,
                    follow_redirects=False,
                )
            except httpx.TransportError:
                if attempt == self.retries:
                    raise GitHubError(
                        "GitHub network request failed or timed out."
                    ) from None
                self.wait(2**attempt)
                continue
            if "x-ratelimit-remaining" in response.headers:
                self.quota = {
                    "remaining": response.headers["x-ratelimit-remaining"],
                    "reset": response.headers.get("x-ratelimit-reset"),
                }
            if response.is_success:
                return response
            # Secondary limits can be a 403 with no Retry-After header.
            limited = response.status_code == HTTPStatus.TOO_MANY_REQUESTS or (
                response.status_code == HTTPStatus.FORBIDDEN
                and (
                    response.headers.get("x-ratelimit-remaining") == "0"
                    or "retry-after" in response.headers
                    or "rate limit" in response.text.lower()
                )
            )
            if (
                not (limited or response.status_code in RETRYABLE)
                or attempt == self.retries
            ):
                message = f"GitHub HTTP {response.status_code} for {urlsplit(url).path}"
                raise GitHubError(message)
            delay = float(2**attempt)
            retry_after = response.headers.get("retry-after")
            if retry_after:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
            elif limited:
                reset = response.headers.get("x-ratelimit-reset")
                delay = (
                    float(reset) - time.time() + 1
                    if reset and self.quota.get("remaining") == "0"
                    else 60
                )
            if delay > self.max_wait:
                raise GitHubError(
                    "GitHub rate limit requires a longer pause; rerun later."
                )
            self.wait(max(0, delay))
        raise GitHubError("GitHub retries exhausted.")  # pragma: no cover

    def pages(self, path: str) -> Iterator[list[Json]]:
        """Follow Link headers to exhaustion, without search's 1,000-result limit."""
        seen: set[str] = set()
        while path:
            if path in seen:
                raise GitHubError("Repeated GitHub pagination link.")
            seen.add(path)
            response = self.request(path)
            data = response.json()
            if not isinstance(data, list):
                raise GitHubError("Expected a GitHub list response.")
            yield data
            path = response.links.get("next", {}).get("url", "")

    def listing(self, path: str) -> list[Json]:
        return [item for page in self.pages(path) for item in page]

    def pull_signals(self, ids: list[str]) -> dict[str, Json]:
        """Batch current PR review, mergeability and head-commit check rollup."""
        result: dict[str, Json] = {}
        batch_size = 20
        for offset in range(0, len(ids), batch_size):
            batch = ids[offset : offset + batch_size]
            data = self.request(
                "/graphql",
                {
                    "query": """query($ids:[ID!]!){nodes(ids:$ids){... on PullRequest{
                      id number reviewDecision mergeable mergeStateStatus
                      commits(last:1){nodes{commit{oid statusCheckRollup{state}}}}
                    }}}""",
                    "variables": {"ids": batch},
                },
            ).json()
            nodes = (data.get("data") or {}).get("nodes")
            if (
                data.get("errors")
                or not isinstance(nodes, list)
                or len(nodes) != len(batch)
                or any(not node or not node.get("id") for node in nodes)
            ):
                raise GitHubError("GitHub returned incomplete pull request signals.")
            result.update({node["id"]: node for node in nodes})
        return result
