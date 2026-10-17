"""Sync orchestration: source I/O stays outside short database transactions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.github import GitHubError, GitHubSource
from gh_maintainer.repositories import CatalogRepository, SyncJournal, SyncLease
from gh_maintainer.schemas import (
    ItemSnapshot,
    Kind,
    PullPayload,
    RepositoryPayload,
    RepositorySnapshot,
    State,
    SyncResult,
)

OVERLAP = timedelta(minutes=5)
RECONCILE_AFTER = timedelta(days=7)


class SyncService:
    def __init__(
        self,
        database: Database,
        source: GitHubSource,
        progress: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.database, self.source, self.progress = database, source, progress
        self.catalog = CatalogRepository(database)
        self.journal = SyncJournal(database)

    async def _repository(
        self, repo: RepositorySnapshot, force_full: bool, lease: SyncLease
    ) -> str | None:
        previous = await self.catalog.repo(repo.id)
        started_at = now()
        full = (
            force_full
            or previous.synced_at is None
            or previous.reconciled_at is None
            or started_at - previous.reconciled_at >= RECONCILE_AFTER
        )
        since = (
            previous.synced_at - OVERLAP if previous.synced_at and not full else None
        )
        existing = {item.id: item for item in await self.catalog.items(repo.id)}
        rows: dict[str, ItemSnapshot] = {}
        async for batch in self.source.issues(repo, since):
            for incoming in batch:
                item = incoming
                old = existing.get(item.id)
                if old:
                    item = item.model_copy(
                        update={
                            "pr": old.pr,
                            "signals": old.signals,
                            "signals_at": old.signals_at,
                        }
                    )
                rows[item.id] = item
        if not full:
            for item_id, item in existing.items():
                if item.kind == Kind.PR and item.state == State.OPEN and item.available:
                    rows.setdefault(item_id, item)
        open_prs: dict[str, str] = {}
        for item_id, item in list(rows.items()):
            if item.kind != Kind.PR:
                continue
            raw = await self.source.pull(repo, item.number)
            pull = PullPayload.model_validate(raw)
            rows[item_id] = item.with_pull(raw)
            if rows[item_id].state == State.OPEN:
                open_prs[item_id] = pull.node_id
        signals_error = None
        try:
            signals = await self.source.signals(list(open_prs.values()))
            if set(signals) != set(open_prs.values()):
                raise GitHubError("GitHub returned incomplete pull request signals.")
            for item_id, node_id in open_prs.items():
                rows[item_id] = rows[item_id].model_copy(
                    update={"signals": signals[node_id], "signals_at": started_at}
                )
        except (GitHubError, ValueError) as error:
            signals_error = f"PR signals incomplete: {error}"
        await self.catalog.commit_repo(
            repo.id, list(rows.values()), started_at, bool(full), signals_error, lease
        )
        return signals_error

    async def run(self, full: bool = False, only: set[str] | None = None) -> SyncResult:
        async with SyncLease(self.database) as lease:
            run_id = await self.journal.start(lease)
            errors: list[str] = []
            synced = 0
            try:
                owner = await self.source.account()
                repos: list[RepositorySnapshot] = []
                async for batch in self.source.repositories():
                    repos.extend(
                        repo
                        for repo in batch
                        if RepositoryPayload.model_validate(
                            repo.raw
                        ).owner.login.lower()
                        == owner.login.lower()
                    )
                if only and only - {repo.name for repo in repos}:
                    raise ValueError(
                        "Requested repositories are not visible in the owned inventory."
                    )
                await self.catalog.discover(owner, repos, lease)
                for repo in repos:
                    if only and repo.name not in only:
                        continue
                    self.progress(f"Syncing {repo.name}")
                    try:
                        error = await self._repository(repo, full, lease)
                        synced += 1
                        if error:
                            errors.append(f"{repo.name}: {error}")
                    except (GitHubError, ValueError) as error:
                        await self.catalog.fail_repo(repo.id, str(error), lease)
                        errors.append(f"{repo.name}: {error}")
                status: Literal["partial", "complete"] = (
                    "partial" if errors else "complete"
                )
                await self.journal.finish(
                    run_id, status, self.source.calls, "\n".join(errors) or None, lease
                )
                return SyncResult(
                    owner=owner.login,
                    discovered=len(repos),
                    synced=synced,
                    status=status,
                    errors=errors,
                    source_calls=self.source.calls,
                )
            except BaseException as error:
                await self.journal.finish(
                    run_id, "failed", self.source.calls, str(error), lease
                )
                raise
