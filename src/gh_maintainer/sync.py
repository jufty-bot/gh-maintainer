"""Sync orchestration: source I/O stays outside short database transactions."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from inspect import isawaitable
from time import monotonic
from typing import Literal

import anyio
import structlog
from structlog.typing import FilteringBoundLogger

from gh_maintainer.clock import now, timestamp
from gh_maintainer.concurrency import map_concurrently
from gh_maintainer.database import Database
from gh_maintainer.github import (
    DEFAULT_CONCURRENCY,
    SIGNAL_BATCH_SIZE,
    GitHubError,
    GitHubSource,
)
from gh_maintainer.repositories import CatalogRepository, SyncJournal, SyncLease
from gh_maintainer.schemas import (
    CommentSnapshot,
    ItemSnapshot,
    Kind,
    PullCodeSnapshot,
    PullPayload,
    RepositoryPayload,
    RepositorySnapshot,
    State,
    SyncResult,
    owner_name,
    repository_name,
    source_content,
)

OVERLAP = timedelta(minutes=5)
RECONCILE_AFTER = timedelta(days=7)
logger = structlog.get_logger(__name__)


@dataclass
class RepositoryBatch:
    repo: RepositorySnapshot
    rows: dict[str, ItemSnapshot]
    comments: list[CommentSnapshot]
    review_numbers: set[int]
    open_prs: dict[str, str]
    started_at: datetime
    started: float
    full: bool
    comments_full: bool
    log: FilteringBoundLogger
    code: dict[str, PullCodeSnapshot]
    signals_error: str | None = None


class SyncService:
    def __init__(
        self,
        database: Database,
        source: GitHubSource,
        progress: Callable[[str], Awaitable[None] | None] = lambda _: None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        if concurrency < 1:
            raise ValueError("Concurrency must be at least 1.")
        self.concurrency = concurrency
        self.database, self.source, self.progress = database, source, progress
        self.catalog = CatalogRepository(database)
        self.journal = SyncJournal(database)

    async def _progress(self, message: str) -> None:
        result = self.progress(message)
        if isawaitable(result):
            await result

    async def _repository(
        self,
        repo: RepositorySnapshot,
        force_full: bool,
        log: FilteringBoundLogger,
    ) -> RepositoryBatch:
        started = monotonic()
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
        log = log.bind(mode="full" if full else "incremental")
        log.info(
            "Repository sync started",
            since=timestamp(since) if since is not None else None,
        )
        rows: dict[str, ItemSnapshot] = {}
        async for batch in self.source.issues(repo, since):
            rows.update((item.id, item) for item in batch)
            log.info("Issue batch fetched", batch_items=len(batch), items=len(rows))
            await self._progress(f"{repo.name}: fetched {len(rows)} items")
        existing = {
            item.id: item
            for item in await self.catalog.items(
                repo.id, set(rows), include_prs=not full
            )
        }
        code_refs = await self.catalog.code_refs(repo.id) if not full else {}
        code: dict[str, PullCodeSnapshot] = {}
        for item_id, item in rows.items():
            old = existing.get(item_id)
            if old:
                rows[item_id] = item.model_copy(
                    update={
                        "pr": old.pr,
                        "signals": old.signals,
                        "signals_at": old.signals_at,
                    }
                )
        if not full:
            for item_id, item in existing.items():
                if (
                    item.kind == Kind.PR
                    and item.available
                    and (item.state == State.OPEN or item.id not in code_refs)
                ):
                    rows.setdefault(item_id, item)
        comments_full = full or previous.comments_synced_at is None
        comments_since = (
            previous.comments_synced_at - OVERLAP
            if previous.comments_synced_at and not comments_full
            else None
        )
        comments: list[CommentSnapshot] = []
        by_number = {item.number: item for item in [*existing.values(), *rows.values()]}
        log.info(
            "Fetching comments",
            since=timestamp(comments_since) if comments_since is not None else None,
        )
        async for comment_batch in self.source.comments(repo, comments_since):
            missing = {comment.number for comment in comment_batch} - by_number.keys()
            for parent in await self.catalog.parents(repo.id, missing):
                by_number[parent.number] = parent
                existing[parent.id] = parent
            for comment in comment_batch:
                if (
                    comment.number not in by_number
                    or not by_number[comment.number].available
                    or (full and by_number[comment.number].id not in rows)
                ):
                    parent = await self.source.issue(repo, comment.number)
                    rows[parent.id] = parent
                    by_number[parent.number] = parent
                comments.append(comment)
            await self._progress(f"{repo.name}: fetched {len(comments)} comments")
            log.info(
                "Comment batch fetched",
                batch_comments=len(comment_batch),
                comments=len(comments),
            )
        candidates = rows.values() if full else by_number.values()
        review_numbers = {
            item.number
            for item in candidates
            if item.kind == Kind.PR and item.available
        }
        if not full:
            review_numbers.update(await self.catalog.pull_numbers(repo.id))
        log.info("Fetching pull request reviews")

        async def refresh_reviews(number: int) -> None:
            async for review_batch in self.source.reviews(repo, number):
                comments.extend(review_batch)
                await self._progress(f"{repo.name}: fetched reviews for #{number}")
            log.debug("Pull request reviews fetched", number=number)

        await map_concurrently(
            refresh_reviews,
            sorted(review_numbers),
            self.concurrency,
        )
        open_prs: dict[str, str] = {}
        log.info(
            "Refreshing pull requests",
            pull_requests=sum(item.kind == Kind.PR for item in rows.values()),
        )

        async def refresh_pull(item: ItemSnapshot) -> None:
            old = existing.get(item.id)
            # Closed PRs in the overlap window often have not changed. Reapply the
            # cached detail to preserve merged state; open PRs always get fresh data.
            if (
                not full
                and old is not None
                and State.OPEN not in (old.state, item.state)
                and source_content(old.raw) == source_content(item.raw)
                and old.pr is not None
                and item.id in code_refs
            ):
                raw = old.pr
            else:
                raw = await self.source.pull(repo, item.number)
            pull = PullPayload.model_validate(raw)
            log.debug("Pull request refreshed", number=item.number)
            await self._progress(f"{repo.name}: refreshed pull request #{item.number}")
            rows[item.id] = item.with_pull(raw)
            if full or code_refs.get(item.id) != (pull.head.sha, pull.base.sha):
                code[item.id] = await self.source.pull_code(repo, item.number, raw)
            if rows[item.id].state == State.OPEN:
                open_prs[item.id] = pull.node_id

        await map_concurrently(
            refresh_pull,
            [item for item in rows.values() if item.kind == Kind.PR],
            self.concurrency,
        )
        log.info("Fetching pull request signals", open_pull_requests=len(open_prs))
        return RepositoryBatch(
            repo,
            rows,
            comments,
            review_numbers,
            open_prs,
            started_at,
            started,
            bool(full),
            comments_full,
            log,
            code,
        )

    async def _signals(self, repositories: list[RepositoryBatch]) -> None:
        # Share requests across the bounded window of staged repositories. Do not
        # stage the entire inventory, or a hundred backfills would exhaust memory.
        by_node = {
            node_id: batch
            for batch in repositories
            for node_id in batch.open_prs.values()
        }
        node_ids = list(by_node)
        signals = {}
        for offset in range(0, len(node_ids), SIGNAL_BATCH_SIZE):
            ids = node_ids[offset : offset + SIGNAL_BATCH_SIZE]
            await self._progress(f"Fetching signals for {len(ids)} pull requests")
            try:
                fetched = await self.source.signals(ids)
                if set(fetched) != set(ids):
                    raise GitHubError(
                        "GitHub returned incomplete pull request signals."
                    )
                signals.update(fetched)
            except (GitHubError, ValueError) as error:
                for node_id in ids:
                    by_node[node_id].signals_error = f"PR signals incomplete: {error}"
        for batch in repositories:
            if batch.signals_error:
                batch.log.warning(
                    "Pull request signals incomplete", error=batch.signals_error
                )
                continue
            for item_id, node_id in batch.open_prs.items():
                batch.rows[item_id] = batch.rows[item_id].model_copy(
                    update={"signals": signals[node_id], "signals_at": batch.started_at}
                )

    async def _commit(self, batch: RepositoryBatch, lease: SyncLease) -> None:
        await self.catalog.commit_repo(
            batch.repo.id,
            list(batch.rows.values()),
            batch.started_at,
            batch.full,
            batch.signals_error,
            lease,
            comments=batch.comments,
            comments_full=batch.comments_full,
            review_numbers=batch.review_numbers,
            code=batch.code,
        )
        batch.log.info(
            "Repository sync finished",
            status="partial" if batch.signals_error else "complete",
            items=len(batch.rows),
            comments=len(batch.comments),
            duration_seconds=round(monotonic() - batch.started, 3),
        )

    async def _window(
        self,
        repos: list[RepositorySnapshot],
        full: bool,
        lease: SyncLease,
        log: FilteringBoundLogger,
    ) -> list[tuple[bool, str | None]]:
        """Stage at most one concurrency window and release it before the next."""

        async def ingest(
            repo: RepositorySnapshot,
        ) -> tuple[RepositoryBatch | None, str | None]:
            await self._progress(f"Syncing {repo.name}")
            repo_log = log.bind(repository=repo.name, repository_id=repo.id)
            try:
                return await self._repository(repo, full, repo_log), None
            except (GitHubError, ValueError) as error:
                await self.catalog.fail_repo(repo.id, str(error), lease)
                repo_log.error("Repository sync failed", error=str(error))
                return None, f"{repo.name}: {error}"

        staged = await map_concurrently(ingest, repos, self.concurrency)
        await self._signals([batch for batch, _ in staged if batch is not None])
        results = []
        # SQLite has one writer. Commit in inventory order, keeping errors
        # deterministic and independent repository data atomic.
        for batch, fetch_error in staged:
            if batch is None:
                results.append((False, fetch_error))
                continue
            try:
                await self._commit(batch, lease)
                await self._progress(f"Synced {batch.repo.name}")
                results.append(
                    (
                        True,
                        f"{batch.repo.name}: {batch.signals_error}"
                        if batch.signals_error
                        else None,
                    )
                )
            except (GitHubError, ValueError) as failure:
                await self.catalog.fail_repo(batch.repo.id, str(failure), lease)
                batch.log.error("Repository sync failed", error=str(failure))
                results.append((False, f"{batch.repo.name}: {failure}"))
        return results

    async def _activity(
        self,
        repos: list[RepositorySnapshot],
        lease: SyncLease,
        log: FilteringBoundLogger,
    ) -> list[str]:
        failures = []
        for offset in range(0, len(repos), SIGNAL_BATCH_SIZE):
            batch = repos[offset : offset + SIGNAL_BATCH_SIZE]
            observed_at = now()
            snapshots = {}
            errors = {}
            try:
                snapshots = await self.source.repository_activity(batch)
                if snapshots.keys() - {repo.id for repo in batch}:
                    raise GitHubError("GitHub returned unexpected repository activity.")
                for repo in batch:
                    if repo.id not in snapshots:
                        errors[repo.id] = (
                            "Repository activity incomplete: no complete snapshot."
                        )
            except (GitHubError, ValueError) as error:
                snapshots = {}
                errors = {
                    repo.id: f"Repository activity incomplete: {error}"
                    for repo in batch
                }
            await self.catalog.commit_activity(snapshots, errors, observed_at, lease)
            for repo in batch:
                if repo.id in errors:
                    failures.append(f"{repo.name}: {errors[repo.id]}")
                    log.warning(
                        "Repository activity incomplete",
                        repository=repo.name,
                        error=errors[repo.id],
                    )
        return failures

    async def run(
        self,
        full: bool = False,
        only: set[str] | None = None,
        owner: str | None = None,
    ) -> SyncResult:
        started = monotonic()
        if only is not None and owner is not None:
            raise ValueError("Select either repositories or an owner, not both.")
        if only is not None:
            if not only:
                raise ValueError("Select at least one repository.")
            only = {repository_name(name) for name in only}
        if owner is not None:
            owner = owner_name(owner)
        async with SyncLease(self.database) as lease:
            run_id = await self.journal.start(lease)
            log = logger.bind(run_id=run_id)
            errors: list[str] = []
            synced = 0
            try:
                log.info(
                    "Sync started",
                    full=full,
                    repositories=sorted(only or ()),
                    owner=owner,
                )
                await self._progress("Discovering repositories")
                log.info("Discovering repositories")
                repos: list[RepositorySnapshot] = []
                account = None
                public_only = False
                if only:

                    async def resolve(
                        name: str,
                    ) -> tuple[RepositorySnapshot | None, str | None]:
                        try:
                            return await self.source.repository(name), None
                        except (GitHubError, ValueError) as error:
                            return None, str(error)

                    names = sorted(only)
                    selections = await map_concurrently(
                        resolve, names, self.concurrency
                    )
                    for name, (selected_repo, selection_error) in zip(
                        names, selections, strict=True
                    ):
                        if selected_repo is not None:
                            repos.append(selected_repo)
                        else:
                            errors.append(f"{name}: {selection_error}")
                            await self.catalog.fail_selection(
                                name, str(selection_error), lease
                            )
                            log.error(
                                "Repository selection failed",
                                repository=name,
                                error=selection_error,
                            )
                else:
                    account = await self.source.account(owner)
                    log = log.bind(owner=account.login)
                    if owner and account.kind == "User":
                        try:
                            authenticated = await self.source.account()
                            public_only = authenticated.id != account.id
                        except GitHubError:
                            # App credentials may lack /user access.
                            public_only = True
                    async for batch in self.source.repositories(account):
                        await self._progress(
                            f"Discovered {len(repos) + len(batch)} repositories"
                        )
                        for repo in batch:
                            payload = RepositoryPayload.model_validate(repo.raw)
                            if payload.owner.id == account.id:
                                repos.append(repo)
                # Aliases and redirects may resolve multiple names to the same ID.
                repos = list({repo.id: repo for repo in repos}.values())
                await self.catalog.discover(
                    account, repos, lease, public_only=public_only
                )
                log.info("Repository discovery finished", discovered=len(repos))
                await self._progress(
                    f"Refreshing activity for {len(repos)} repositories"
                )
                errors.extend(await self._activity(repos, lease, log))

                for offset in range(0, len(repos), self.concurrency):
                    outcomes = await self._window(
                        repos[offset : offset + self.concurrency], full, lease, log
                    )
                    synced += sum(success for success, _ in outcomes)
                    errors.extend(error for _, error in outcomes if error)
                status: Literal["partial", "complete"] = (
                    "partial" if errors else "complete"
                )
                await self.journal.finish(
                    run_id, status, self.source.calls, "\n".join(errors) or None, lease
                )
                log.log(
                    logging.WARNING if errors else logging.INFO,
                    "Sync finished",
                    status=status,
                    discovered=len(repos),
                    synced=synced,
                    errors=len(errors),
                    source_calls=self.source.calls,
                    duration_seconds=round(monotonic() - started, 3),
                )
                await self._progress(
                    f"Sync {status}: {synced} of {len(repos)} repositories synced"
                )
                return SyncResult(
                    owner=account.login if account else None,
                    scope="owner" if account else "repositories",
                    coverage="explicit"
                    if only
                    else ("public-only" if public_only else "credential-visible"),
                    discovered=len(repos),
                    synced=synced,
                    status=status,
                    errors=errors,
                    source_calls=self.source.calls,
                )
            except BaseException as error:
                log.exception(
                    "Sync failed",
                    error=str(error),
                    source_calls=self.source.calls,
                    duration_seconds=round(monotonic() - started, 3),
                )
                with anyio.CancelScope(shield=True):
                    await self.journal.finish(
                        run_id, "failed", self.source.calls, str(error), lease
                    )
                raise
