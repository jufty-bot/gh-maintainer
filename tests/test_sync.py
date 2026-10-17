"""Check repository atomicity, lifecycle state and successful-only checkpoints."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from gh_maintainer.github import GitHub
from gh_maintainer.report import snapshot
from gh_maintainer.store import Store
from gh_maintainer.sync import synchronize

Json = dict[str, Any]
AT = "2026-10-01T00:00:00Z"
REPO = {
    "id": 1,
    "full_name": "owner/project",
    "html_url": "https://github.com/owner/project",
    "owner": {"login": "owner"},
    "archived": False,
}


def issue(number: int = 1, pr: bool = False, state: str = "open") -> Json:
    result = {
        "id": number,
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
        result["pull_request"] = {
            "url": f"https://api.github.com/repos/owner/project/pulls/{number}"
        }
    return result


class Scenario:
    def __init__(self) -> None:
        self.issues = [issue(), issue(2, True), issue(3, True, "closed")]
        self.inventory = [REPO]
        self.checks = "SUCCESS"
        self.fail = False
        self.fail_signals = False
        self.queries: list[str] = []
        self.login = "owner"
        self.user_id = 77
        self.github = GitHub(
            "secret",
            client=httpx.Client(transport=httpx.MockTransport(self.handle)),
            retries=0,
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": self.login, "id": self.user_id})
        if path == "/user/repos":
            return httpx.Response(200, json=self.inventory)
        if path.endswith("/issues"):
            self.queries.append(str(request.url))
            if self.fail:
                return httpx.Response(503)
            return httpx.Response(200, json=self.issues)
        if "/pulls/" in path:
            number = int(path.split("/")[-1])
            return httpx.Response(
                200,
                json={
                    "node_id": f"PR_{number}",
                    "state": "closed" if number == 3 else "open",
                    "number": number,
                    "updated_at": AT,
                    "merged_at": AT if number == 3 else None,
                    "head": {"sha": "abc"},
                    "draft": False,
                },
            )
        if path == "/graphql":
            if self.fail_signals:
                return httpx.Response(200, json={"errors": [{"message": "denied"}]})
            ids = json.loads(request.content)["variables"]["ids"]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "nodes": [
                            {
                                "id": node_id,
                                "number": int(node_id.split("_")[-1]),
                                "reviewDecision": "REVIEW_REQUIRED",
                                "mergeable": "MERGEABLE",
                                "mergeStateStatus": "CLEAN",
                                "commits": {
                                    "nodes": [
                                        {
                                            "commit": {
                                                "oid": "abc",
                                                "statusCheckRollup": {
                                                    "state": self.checks
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                            for node_id in ids
                        ]
                    }
                },
            )
        message = f"Unexpected API path: {path}"
        raise AssertionError(message)


def test_backfill_idempotence_and_merged_state(store: Store) -> None:
    scenario = Scenario()
    result = synchronize(store, scenario.github)
    assert result["status"] == "complete"
    assert len(store.items("1")) == 3
    assert (
        store.db.execute("SELECT state FROM items WHERE id='I_3'").fetchone()[0]
        == "merged"
    )
    assert store.db.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 3
    synchronize(store, scenario.github, full=True)
    assert store.db.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 3


def test_incremental_refreshes_ci_without_issue_change(store: Store) -> None:
    scenario = Scenario()
    synchronize(store, scenario.github)
    scenario.issues = []
    scenario.checks = "FAILURE"
    synchronize(store, scenario.github)
    assert "since=" in scenario.queries[-1]
    data = snapshot(store)
    pr = next(row for row in data["items"] if row["id"] == "I_2")
    assert pr["checks"] == "FAILURE"
    assert "Failing checks" in pr["reasons"]
    assert store.db.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 4


def test_repository_failure_does_not_advance_checkpoint(store: Store) -> None:
    scenario = Scenario()
    synchronize(store, scenario.github)
    before = store.repo("1")["synced_at"]
    scenario.fail = True
    result = synchronize(store, scenario.github)
    assert result["status"] == "partial"
    assert store.repo("1")["synced_at"] == before
    assert len(store.items("1")) == 3
    assert snapshot(store)["counts"]["incomplete_repositories"] == 1
    scenario.fail = False
    assert synchronize(store, scenario.github)["status"] == "complete"
    assert store.repo("1")["error"] is None


def test_signals_failure_preserves_timestamp_and_marks_partial(store: Store) -> None:
    scenario = Scenario()
    synchronize(store, scenario.github)
    before = store.repo("1")["signals_at"]
    scenario.fail_signals = True
    result = synchronize(store, scenario.github)
    assert result["status"] == "partial"
    assert store.repo("1")["signals_at"] == before
    assert "signals incomplete" in store.repo("1")["error"]


def test_reconciliation_retains_missing_items_and_repositories(store: Store) -> None:
    scenario = Scenario()
    synchronize(store, scenario.github)
    scenario.issues = []
    synchronize(store, scenario.github, full=True)
    assert len(store.items("1")) == 3
    assert all(not row["available"] for row in store.items("1"))
    scenario.inventory = []
    synchronize(store, scenario.github)
    assert not store.repo("1")["present"]
    assert snapshot(store)["counts"]["repositories"] == 0


def test_lease_prevents_concurrent_sync_and_is_released(tmp_path: Path) -> None:
    first, second = Store(tmp_path / "test.db"), Store(tmp_path / "test.db")
    with first.sync_lock():
        with pytest.raises(RuntimeError, match="Another sync"), second.sync_lock():
            pass
    with second.sync_lock():
        second.renew_lease()
    first.close()
    second.close()


def test_owner_mismatch_and_bad_items_are_explicit_failures(store: Store) -> None:
    scenario = Scenario()
    synchronize(store, scenario.github)
    scenario.login = "other"
    scenario.user_id = 88
    with pytest.raises(ValueError, match="different owner"):
        synchronize(store, scenario.github)
    scenario.login = "owner"
    scenario.user_id = 77
    scenario.issues = [{"id": 1}]
    result = synchronize(store, scenario.github)
    assert result["status"] == "partial"
    assert len(store.items("1")) == 3


def test_owner_rename_preserves_stable_account_identity(store: Store) -> None:
    scenario = Scenario()
    synchronize(store, scenario.github)
    scenario.login = "renamed"
    scenario.inventory = [
        {**REPO, "owner": {"login": "renamed"}, "full_name": "renamed/project"}
    ]
    synchronize(store, scenario.github)
    assert store.owner() == "renamed"
    assert store.repo("1")["name"] == "renamed/project"
    assert len(store.items("1")) == 3
