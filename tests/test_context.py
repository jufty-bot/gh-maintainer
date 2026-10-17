"""Bounded source evidence, commit identity, partial coverage, and CLI integration."""

import asyncio
import base64
import copy
import json
import subprocess
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gh_maintainer import cli, context
from gh_maintainer.context import ContextError, ContextRequest, collect_context
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.schemas import Kind
from gh_maintainer.sync import SyncService

from .fakes import FakeSource

pytestmark = pytest.mark.anyio


@pytest.fixture
async def item(database):
    await SyncService(database, FakeSource()).run()
    async with database.sessions() as session:
        return (await CatalogQueries(session).show("I_2")).item


@pytest.fixture
def api(monkeypatch):
    calls = []
    remote = {
        "head": {"sha": "abc", "repo": {"full_name": "fork/project"}},
        "base": {"sha": "base"},
        "html_url": "https://github.com/owner/project/pull/2",
        "changed_files": 2,
    }

    def fetch(endpoint, payload=None):
        calls.append((endpoint, payload))
        if "/pulls/2/files" in endpoint:
            return [
                {"filename": "src/file.py", "status": "modified", "patch": "-old\n+new"}
            ]
        if endpoint.endswith("/pulls/2"):
            return copy.deepcopy(remote)
        if "/contents/" in endpoint:
            return {
                "encoding": "base64",
                "content": base64.b64encode(b"Applicable guidance or code").decode(),
                "sha": "blob",
                "html_url": "https://github.com/owner/project/blob/base/AGENTS.md",
            }
        if "/git/trees/" in endpoint:
            return {"tree": [], "truncated": False}
        if "check-runs?" in endpoint:
            return {
                "total_count": 1,
                "check_runs": [{"head_sha": "abc", "conclusion": "failure"}],
            }
        if "/status?" in endpoint:
            return {"total_count": 0, "statuses": []}
        if "/actions/runs?" in endpoint:
            return {"total_count": 0, "workflow_runs": []}
        if endpoint.endswith("/jobs/5"):
            return {"head_sha": "old", "id": 5}
        if endpoint.endswith("/logs"):
            return "job output"
        if endpoint == "graphql":
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "totalCount": 2,
                                "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                                "nodes": [
                                    {
                                        "id": "thread",
                                        "isResolved": False,
                                        "comments": {
                                            "pageInfo": {
                                                "hasNextPage": True,
                                                "endCursor": "reply",
                                            },
                                            "nodes": [],
                                        },
                                    }
                                ],
                            }
                        }
                    }
                }
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(context, "gh_api", fetch)
    return fetch, calls, remote


async def test_files_pagination_omitted_patch_and_budget(item, api, monkeypatch):
    fetch, _, _ = api
    page = collect_context(item, ContextRequest(section="files", limit=1))
    assert not page.complete and page.next_page == 2
    assert page.head_sha == "abc" and page.base_sha == "base"
    assert page.indexed_revision == 1 and not page.head_changed
    clipped = collect_context(
        item, ContextRequest(section="file", path="src/file.py", max_chars=5)
    )
    assert clipped.truncated and not clipped.complete

    def no_patch(endpoint, payload=None):
        if "/files?" in endpoint:
            return [{"filename": "large.bin", "status": "modified"}]
        return fetch(endpoint, payload)

    monkeypatch.setattr(context, "gh_api", no_patch)
    missing = collect_context(item, ContextRequest(section="files"))
    assert not missing.complete and "Patch unavailable" in missing.missing[0]


async def test_fork_files_and_base_guidance_are_bound_to_shas(item, api):
    _, calls, _ = api
    result = collect_context(item, ContextRequest(section="file", path="src/a b.py"))
    assert result.complete
    assert any(
        "repos/fork/project/contents/src/a%20b.py?ref=abc" == endpoint
        for endpoint, _ in calls
    )
    result = collect_context(
        item, ContextRequest(section="guidance", path="src/nested/file.py")
    )
    assert result.complete
    assert any(
        "repos/owner/project/contents/src/nested/AGENTS.md?ref=base" == endpoint
        for endpoint, _ in calls
    )
    for path in ["../secret", "/absolute", "dir\\file"]:
        assert not collect_context(
            item, ContextRequest(section="file", path=path)
        ).complete


async def test_changing_head_or_base_and_index_mismatch(item, api, monkeypatch):
    fetch, _, remote = api
    remote["head"]["sha"] = "new"
    assert collect_context(item, ContextRequest()).head_changed
    count = 0

    def changing(endpoint, payload=None):
        nonlocal count
        data = fetch(endpoint, payload)
        if endpoint.endswith("/pulls/2"):
            count += 1
            if count > 1:
                data["base"]["sha"] = "changed-base"
        return data

    monkeypatch.setattr(context, "gh_api", changing)
    packet = collect_context(item, ContextRequest(section="files"))
    assert not packet.stable and not packet.complete


async def test_checks_threads_and_old_head_logs(item, api):
    _, calls, _ = api
    assert collect_context(item, ContextRequest(section="checks")).complete
    threads = collect_context(
        item, ContextRequest(section="threads", cursor="previous")
    )
    assert threads.next_cursor == "next" and not threads.complete
    assert threads.missing
    payload = next(payload for endpoint, payload in calls if endpoint == "graphql")
    assert payload["variables"]["cursor"] == "previous"
    logs = collect_context(item, ContextRequest(section="logs", job_id=5))
    assert not logs.complete and "different head" in logs.missing[0]
    assert not any(endpoint.endswith("/logs") for endpoint, _ in calls)


async def test_known_absent_guidance_and_failed_credentials(item, api, monkeypatch):
    fetch, _, _ = api

    def absent(endpoint, payload=None):
        if "/contents/" in endpoint:
            raise ContextError("404")
        return fetch(endpoint, payload)

    monkeypatch.setattr(context, "gh_api", absent)
    packet = collect_context(item, ContextRequest(section="guidance"))
    assert packet.complete and len(packet.evidence["absent_at_base"]) == 3

    def denied(_endpoint, _payload=None):
        raise ContextError("403")

    monkeypatch.setattr(context, "gh_api", denied)
    assert not collect_context(item, ContextRequest()).complete


async def test_404_does_not_prove_guidance_absence(item, api, monkeypatch):
    fetch, _, _ = api

    def inaccessible(endpoint, payload=None):
        if "/contents/" in endpoint:
            raise ContextError("404")
        if "/git/trees/" in endpoint:
            return {
                "tree": [{"path": "AGENTS.md", "sha": "blob", "type": "blob"}],
                "truncated": False,
            }
        return fetch(endpoint, payload)

    monkeypatch.setattr(context, "gh_api", inaccessible)
    packet = collect_context(item, ContextRequest(section="guidance"))
    assert not packet.complete and "AGENTS.md" not in packet.evidence["absent_at_base"]
    assert any("AGENTS.md" in gap for gap in packet.missing)


async def test_matching_job_logs_and_checks_continuation(item, api, monkeypatch):
    fetch, _, _ = api

    def updated(endpoint, payload=None):
        if endpoint.endswith("/jobs/5"):
            return {"id": 5, "head_sha": "abc"}
        if "check-runs?" in endpoint:
            return {
                "total_count": 3,
                "check_runs": [{"head_sha": "abc", "conclusion": "failure"}],
            }
        return fetch(endpoint, payload)

    monkeypatch.setattr(context, "gh_api", updated)
    packet = collect_context(item, ContextRequest(section="logs", job_id=5))
    assert packet.complete and packet.evidence["text"] == "job output"
    checks = collect_context(item, ContextRequest(section="checks", limit=1))
    assert not checks.complete and checks.next_page == 2


async def test_issue_file_uses_resolved_default_branch_commit(item, api, monkeypatch):
    fetch, calls, _ = api

    def issue_api(endpoint, payload=None):
        if endpoint == "repos/owner/project":
            return {"default_branch": "release/next"}
        if "/commits/release%2Fnext" in endpoint:
            return {"sha": "default-commit"}
        return fetch(endpoint, payload)

    monkeypatch.setattr(context, "gh_api", issue_api)
    issue = item.model_copy(update={"kind": Kind.ISSUE, "pr": None})
    packet = collect_context(issue, ContextRequest(section="file", path="README.md"))
    assert packet.complete and packet.head_sha == packet.base_sha == "default-commit"
    assert any("README.md?ref=default-commit" in endpoint for endpoint, _ in calls)


async def test_gh_transport_is_read_only_and_sanitizes_errors(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert context.gh_api("repos/owner/project") == {"ok": True}
    assert calls[0][0][-2:] == ["--method", "GET"]
    assert "--hostname" in calls[0][0] and "github.com" in calls[0][0]
    context.gh_api("graphql", {"query": "query{viewer{login}}"})
    assert json.loads(calls[-1][1]["input"])["query"].startswith("query")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            returncode=1, stdout="", stderr="private secret (HTTP 403)"
        ),
    )
    with pytest.raises(ContextError, match="403") as error:
        context.gh_api("repos/owner/project")
    assert "secret" not in str(error.value)


@pytest.mark.usefixtures("api")
async def test_context_cli_retains_local_records(database, item):
    path = database.engine.url.database
    result = await asyncio.to_thread(
        CliRunner().invoke,
        cli.main,
        [
            "--db",
            path,
            "context",
            "I_2",
            "--section",
            "files",
            "--limit",
            "1",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["next_page"] == 2
    async with database.sessions() as session:
        assert (await CatalogQueries(session).show("I_2")).item == item
