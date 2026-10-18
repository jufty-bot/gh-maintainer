"""Repository selection preserves independent scopes and previously indexed history."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from gh_maintainer import cli
from gh_maintainer.database import Database
from gh_maintainer.github import GitHubError
from gh_maintainer.models import RepositoryOwner, SyncRun
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import CatalogRepository, TriageService
from gh_maintainer.schemas import (
    AccountSnapshot,
    Disposition,
    JsonObject,
    RepositorySnapshot,
)
from gh_maintainer.sync import SyncService

from .fakes import REPO, FakeSource

OTHER: JsonObject = {
    **REPO,
    "id": 2,
    "full_name": "team/another",
    "html_url": "https://github.com/team/another",
    "owner": {"id": 88, "login": "team", "type": "Organization"},
    "private": False,
}
TEAM = AccountSnapshot(id="88", login="team", kind="Organization")


@pytest.mark.anyio
async def test_explicit_mixed_owners_and_subset_preserve_inventory(
    database: Database,
) -> None:
    class DirectSource(FakeSource):
        async def account(self, _login: str | None = None) -> AccountSnapshot:
            raise AssertionError("Explicit selection must not require /user.")

        async def repositories(
            self, _owner: AccountSnapshot | None = None
        ) -> AsyncIterator[list[RepositorySnapshot]]:
            raise AssertionError("Explicit selection must not enumerate inventories.")
            yield []  # pragma: no cover

    source = DirectSource()
    source.inventory, source.items = [REPO, OTHER], []
    result = await SyncService(database, source).run(
        only={"OWNER/PROJECT", "team/another"}
    )
    assert (result.scope, result.coverage, result.owner) == (
        "repositories",
        "explicit",
        None,
    )
    assert result.status == "complete" and result.synced == 2
    async with database.sessions() as session:
        overview = await CatalogQueries(session).overview()
        assert overview.owner is None
        assert [owner.login for owner in overview.owners] == ["owner", "team"]
    source.inventory = [REPO]
    await SyncService(database, source).run(only={"owner/project"})
    assert (await CatalogRepository(database).repo("2")).present


@pytest.mark.anyio
async def test_owner_reconciliation_is_scoped(database: Database) -> None:
    source = FakeSource()
    source.inventory, source.items = [REPO, OTHER], []
    source.accounts["team"] = TEAM
    await SyncService(database, source).run(only={"owner/project", "team/another"})
    source.inventory = [REPO]
    result = await SyncService(database, source).run(owner="TEAM")
    assert (result.owner, result.scope, result.coverage) == (
        "team",
        "owner",
        "credential-visible",
    )
    catalog = CatalogRepository(database)
    assert (await catalog.repo("1")).present
    assert not (await catalog.repo("2")).present


@pytest.mark.anyio
async def test_public_inventory_preserves_private_and_unknown_visibility(
    database: Database,
) -> None:
    source = FakeSource()
    source.accounts["team"] = AccountSnapshot(id="88", login="team")
    private = {**OTHER, "id": 3, "full_name": "team/private", "private": True}
    unknown = {**OTHER, "id": 4, "full_name": "team/unknown"}
    unknown.pop("private")
    source.inventory, source.items = [OTHER, private, unknown], []
    await SyncService(database, source).run(
        only={"team/another", "team/private", "team/unknown"}
    )
    source.inventory = []
    result = await SyncService(database, source).run(owner="team")
    assert result.coverage == "public-only"
    catalog = CatalogRepository(database)
    assert not (await catalog.repo("2")).present
    assert (await catalog.repo("3")).present
    assert (await catalog.repo("4")).present


@pytest.mark.anyio
async def test_inaccessible_selection_is_partial_and_retains_checkpoints(
    database: Database,
) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    catalog = CatalogRepository(database)
    checkpoint = (await catalog.repo("1")).synced_at
    source.inventory = [OTHER]
    source.items = []
    result = await SyncService(database, source).run(
        only={"owner/project", "team/another"}
    )
    assert result.status == "partial" and result.synced == 1
    assert "owner/project" in result.errors[0] and "404" in result.errors[0]
    assert (await catalog.repo("1")).synced_at == checkpoint
    assert (await catalog.repo("1")).present
    assert len(await catalog.items("1")) == 3
    assert "404" in (await catalog.repo("1")).error
    assert (await catalog.repo("1")).activity_stale
    assert "404" in (await catalog.repo("1")).activity_error


@pytest.mark.anyio
async def test_failed_discovery_does_not_commit_partial_inventory(
    database: Database,
) -> None:
    await SyncService(database, FakeSource()).run()
    catalog = CatalogRepository(database)
    checkpoint = (await catalog.repo("1")).synced_at

    class FailingInventory(FakeSource):
        async def repositories(
            self, _owner: AccountSnapshot | None = None
        ) -> AsyncIterator[list[RepositorySnapshot]]:
            yield [RepositorySnapshot.from_github({**REPO, "full_name": "owner/new"})]
            raise GitHubError("Second inventory page failed.")

    with pytest.raises(GitHubError, match="Second inventory page"):
        await SyncService(database, FailingInventory()).run()
    repo = await catalog.repo("1")
    assert repo.name == "owner/project" and repo.present
    assert repo.synced_at == checkpoint
    async with database.sessions() as session:
        latest = await session.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
        assert latest.status == "failed"


@pytest.mark.anyio
async def test_transfer_aliases_preserve_items_triage_and_membership(
    database: Database,
) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    await TriageService(database).decide("I_1", Disposition.PLANNED, "Keep this note")
    source.inventory = [{**OTHER, "id": 1, "full_name": "team/transferred"}]
    source.aliases["owner/project"] = "team/transferred"
    result = await SyncService(database, source).run(
        only={"owner/project", "team/transferred"}
    )
    assert result.discovered == result.synced == 1
    source.inventory = []
    await SyncService(database, source).run()  # Old owner's empty inventory.
    assert (await CatalogRepository(database).repo("1")).present
    async with database.sessions() as session:
        assert (await session.get(RepositoryOwner, "1")).owner_id == "88"
        detail = await CatalogQueries(session).detail("I_1")
        assert detail.item.repo == "team/transferred"
        assert detail.item.note == "Keep this note"
        assert detail.item.disposition == Disposition.PLANNED


@pytest.mark.anyio
async def test_service_rejects_conflicting_scopes(database: Database) -> None:
    for options in (
        {"only": {"owner/project"}, "owner": "owner"},
        {"only": set()},
        {"only": {"owner/../project"}},
    ):
        with pytest.raises(ValueError):
            await SyncService(database, FakeSource()).run(**options)


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_selection_output_and_exit_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
) -> None:
    class Source(FakeSource):
        closed = False

        async def close(self) -> None:
            self.closed = True

    source = Source()
    source.inventory, source.items = [OTHER], []
    source.accounts["team"] = TEAM

    async def token() -> str:
        return "test-token"

    monkeypatch.setattr(cli, "resolve_token", token)
    monkeypatch.setattr(cli, "AsyncGitHubSource", lambda _token, **_options: source)
    selection = ["--repo", "owner/missing"] if explicit else ["--owner", "team"]
    result = CliRunner().invoke(
        cli.main, ["--db", str(tmp_path / "cli.sqlite"), "sync", *selection]
    )
    assert result.exit_code == (1 if explicit else 0), result.output
    data = json.loads(result.stdout)
    assert data["scope"] == ("repositories" if explicit else "owner")
    assert data["status"] == ("partial" if explicit else "complete")
    if not explicit:
        assert "team/another" in result.stderr
    assert source.closed
