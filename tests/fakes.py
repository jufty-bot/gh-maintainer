"""Source contract fake: orchestration tests do not depend on an SDK's internals."""

from collections.abc import AsyncIterator
from datetime import datetime

from gh_maintainer.github import GitHubError
from gh_maintainer.schemas import (
    AccountSnapshot,
    ItemSnapshot,
    JsonObject,
    RepositorySnapshot,
)

AT = "2026-10-01T00:00:00Z"
REPO: JsonObject = {
    "id": 1,
    "full_name": "owner/project",
    "html_url": "https://github.com/owner/project",
    "owner": {"login": "owner"},
    "archived": False,
}


def issue(number: int = 1, pr: bool = False, state: str = "open") -> JsonObject:
    result: JsonObject = {
        "node_id": f"I_{number}",
        "number": number,
        "state": state,
        "title": f"Item {number}",
        "html_url": f"https://github.com/owner/project/issues/{number}",
        "updated_at": AT,
        "labels": [],
        "assignees": [],
        "body": "Detailed context",
    }
    if pr:
        result["pull_request"] = {}
    return result


class FakeSource:
    def __init__(self) -> None:
        self.calls = 0
        self.login, self.user_id = "owner", "77"
        self.inventory = [REPO]
        self.items = [issue(), issue(2, True), issue(3, True, "closed")]
        self.checks = "SUCCESS"
        self.fail = False
        self.fail_signals = False
        self.fail_after_page = False
        self.windows: list[datetime | None] = []
        self.head_sha = "abc"
        self.signal_head = "abc"

    async def account(self) -> AccountSnapshot:
        self.calls += 1
        return AccountSnapshot(id=self.user_id, login=self.login)

    async def repositories(self) -> AsyncIterator[list[RepositorySnapshot]]:
        self.calls += 1
        yield [RepositorySnapshot.from_github(repo) for repo in self.inventory]

    async def issues(
        self, repo: RepositorySnapshot, since: datetime | None
    ) -> AsyncIterator[list[ItemSnapshot]]:
        self.calls += 1
        self.windows.append(since)
        if self.fail:
            raise GitHubError("GitHub source request failed (503).")
        yield [ItemSnapshot.from_github(raw, repo.id) for raw in self.items]
        if self.fail_after_page:
            raise GitHubError("Second page failed.")

    async def pull(self, _repo: RepositorySnapshot, number: int) -> JsonObject:
        self.calls += 1
        return {
            "node_id": f"PR_{number}",
            "state": "closed" if number == 3 else "open",
            "updated_at": AT,
            "merged_at": AT if number == 3 else None,
            "head": {"sha": self.head_sha},
            "draft": False,
        }

    async def signals(self, ids: list[str]) -> dict[str, JsonObject]:
        self.calls += 1
        if self.fail_signals:
            raise GitHubError("Incomplete GraphQL response.")
        return {
            node_id: {
                "id": node_id,
                "reviewDecision": "REVIEW_REQUIRED",
                "mergeable": "MERGEABLE",
                "commits": {
                    "nodes": [
                        {
                            "commit": {
                                "oid": self.signal_head,
                                "statusCheckRollup": {"state": self.checks},
                            }
                        }
                    ]
                },
            }
            for node_id in ids
        }

    async def close(self) -> None:
        pass
