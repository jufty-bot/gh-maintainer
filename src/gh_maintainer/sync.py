"""Sync orchestration: source I/O stays outside short database transactions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from time import monotonic
from typing import Literal

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.github import GitHubError, GitHubSource
from gh_maintainer.repositories import CatalogRepository, SyncJournal, SyncLease
from gh_maintainer.schemas import (
    CommentSnapshot,
    ItemSnapshot,
    Kind,
    PullPayload,
    RepositoryPayload,
    RepositorySnapshot,
    State,
    SyncResult,
    owner_name,
    repository_name,
)

OVERLAP = timedelta(minutes=5)
RECONCILE_AFTER = timedelta(days=7)
logger = logging.getLogger(__name__)


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
        logger.info(
            "%s: Repository sync started (mode=%s, since=%s)",
            repo.name,
            "full" if full else "incremental",
            since,
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
            logger.info(
                "%s: Issue batch fetched (batch_items=%d, items=%d)",
                repo.name,
                len(batch),
                len(rows),
            )
        if not full:
            for item_id, item in existing.items():
                if item.kind == Kind.PR and item.state == State.OPEN and item.available:
                    rows.setdefault(item_id, item)
        comments_full = full or previous.comments_synced_at is None
        comments_since = (
            previous.comments_synced_at - OVERLAP
            if previous.comments_synced_at and not comments_full
            else None
        )
        comments: list[CommentSnapshot] = []
        by_number = {item.number: item for item in [*existing.values(), *rows.values()]}
        logger.info("%s: Fetching comments (since=%s)", repo.name, comments_since)
        async for comment_batch in self.source.comments(repo, comments_since):
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
            logger.info(
                "%s: Comment batch fetched (batch_comments=%d, comments=%d)",
                repo.name,
                len(comment_batch),
                len(comments),
            )
        review_numbers: set[int] = set()
        candidates = rows.values() if full else by_number.values()
        logger.info("%s: Fetching pull request reviews", repo.name)
        for item in list(candidates):
            if item.kind == Kind.PR and item.available:
                review_numbers.add(item.number)
                async for review_batch in self.source.reviews(repo, item.number):
                    comments.extend(review_batch)
                logger.debug(
                    "%s#%d: Pull request reviews fetched", repo.name, item.number
                )
        open_prs: dict[str, str] = {}
        logger.info(
            "%s: Refreshing pull requests (pull_requests=%d)",
            repo.name,
            sum(item.kind == Kind.PR for item in rows.values()),
        )
        for item_id, item in list(rows.items()):
            if item.kind != Kind.PR:
                continue
            raw = await self.source.pull(repo, item.number)
            pull = PullPayload.model_validate(raw)
            rows[item_id] = item.with_pull(raw)
            logger.debug("%s#%d: Pull request refreshed", repo.name, item.number)
            if rows[item_id].state == State.OPEN:
                open_prs[item_id] = pull.node_id
        signals_error = None
        logger.info(
            "%s: Fetching pull request signals (open_pull_requests=%d)",
            repo.name,
            len(open_prs),
        )
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
            logger.warning("%s: Pull request signals incomplete: %s", repo.name, error)
        await self.catalog.commit_repo(
            repo.id,
            list(rows.values()),
            started_at,
            bool(full),
            signals_error,
            lease,
            comments=comments,
            comments_full=comments_full,
            review_numbers=review_numbers,
        )
        logger.info(
            "%s: Repository sync finished "
            "(status=%s, items=%d, comments=%d, duration=%.3fs)",
            repo.name,
            "partial" if signals_error else "complete",
            len(rows),
            len(comments),
            monotonic() - started,
        )
        return signals_error

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
            errors: list[str] = []
            synced = 0
            try:
                logger.info(
                    "Sync started (run_id=%s, full=%s, repositories=%s, owner=%s)",
                    run_id,
                    full,
                    sorted(only or ()),
                    owner,
                )
                logger.info("Discovering repositories")
                repos: list[RepositorySnapshot] = []
                account = None
                public_only = False
                if only:
                    for name in sorted(only):
                        try:
                            repos.append(await self.source.repository(name))
                        except (GitHubError, ValueError) as error:
                            errors.append(f"{name}: {error}")
                            await self.catalog.fail_selection(name, str(error), lease)
                            logger.error(
                                "%s: Repository selection failed: %s", name, error
                            )
                else:
                    account = await self.source.account(owner)
                    logger.info("Discovering repositories for %s", account.login)
                    if owner and account.kind == "User":
                        try:
                            authenticated = await self.source.account()
                            public_only = authenticated.id != account.id
                        except GitHubError:
                            # App credentials may lack /user access.
                            public_only = True
                    async for batch in self.source.repositories(account):
                        for repo in batch:
                            payload = RepositoryPayload.model_validate(repo.raw)
                            if payload.owner.id == account.id:
                                repos.append(repo)
                # Aliases and redirects may resolve multiple names to the same ID.
                repos = list({repo.id: repo for repo in repos}.values())
                await self.catalog.discover(
                    account, repos, lease, public_only=public_only
                )
                logger.info("Repository discovery finished (discovered=%d)", len(repos))
                for repo in repos:
                    self.progress(f"Syncing {repo.name}")
                    try:
                        signals_error = await self._repository(repo, full, lease)
                        synced += 1
                        if signals_error:
                            errors.append(f"{repo.name}: {signals_error}")
                    except (GitHubError, ValueError) as error:
                        await self.catalog.fail_repo(repo.id, str(error), lease)
                        errors.append(f"{repo.name}: {error}")
                        logger.error("%s: Repository sync failed: %s", repo.name, error)
                status: Literal["partial", "complete"] = (
                    "partial" if errors else "complete"
                )
                await self.journal.finish(
                    run_id, status, self.source.calls, "\n".join(errors) or None, lease
                )
                logger.log(
                    logging.WARNING if errors else logging.INFO,
                    "Sync finished (run_id=%s, status=%s, discovered=%d, synced=%d, "
                    "errors=%d, source_calls=%d, duration=%.3fs)",
                    run_id,
                    status,
                    len(repos),
                    synced,
                    len(errors),
                    self.source.calls,
                    monotonic() - started,
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
                logger.exception(
                    "Sync failed (run_id=%s, source_calls=%d, duration=%.3fs): %s",
                    run_id,
                    self.source.calls,
                    monotonic() - started,
                    error,
                )
                await self.journal.finish(
                    run_id, "failed", self.source.calls, str(error), lease
                )
                raise
