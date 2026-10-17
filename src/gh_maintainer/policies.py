"""Pure triage rules and presentation derived from validated snapshots."""

from datetime import datetime

from gh_maintainer.schemas import (
    Assessment,
    Disposition,
    IssuePayload,
    ItemSnapshot,
    ItemView,
    Kind,
    RepositoryStatus,
    SignalsPayload,
    State,
    TriageSnapshot,
)

STALE_DAYS = 30
SECONDS_PER_DAY = 86400


def assess(
    item: ItemSnapshot, repo: RepositoryStatus, triage: TriageSnapshot, at: datetime
) -> Assessment:
    disposition = triage.disposition
    if (
        disposition == Disposition.SNOOZED
        and triage.snoozed_until
        and triage.snoozed_until <= at
    ):
        disposition = Disposition.INBOX
    if (
        disposition == Disposition.DONE
        and triage.updated_at
        and item.updated_at > triage.updated_at
    ):
        disposition = Disposition.INBOX
    age = max(0, int((at - item.updated_at).total_seconds() / SECONDS_PER_DAY))
    signals = SignalsPayload.model_validate(item.signals) if item.signals else None
    commit = (
        signals.commits.nodes[-1].commit if signals and signals.commits.nodes else None
    )
    pr = item.pr or {}
    head = pr.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    stale = bool(
        signals and (repo.error or (commit and commit.oid and commit.oid != head_sha))
    )
    checks = (
        commit.statusCheckRollup.state if commit and commit.statusCheckRollup else None
    )
    checks = "UNKNOWN" if stale else (checks or "UNKNOWN")
    review = (
        "UNKNOWN"
        if stale
        else ((signals.reviewDecision if signals else None) or "UNKNOWN")
    )
    mergeable = "UNKNOWN" if stale else (signals.mergeable if signals else "UNKNOWN")
    draft = bool(pr.get("draft"))
    issue = IssuePayload.model_validate(item.raw)
    reasons, score = [], 0
    if item.state == State.OPEN:
        rules = [
            (checks in {"FAILURE", "ERROR"}, "Failing checks", 80),
            (mergeable == "CONFLICTING", "Merge conflict", 70),
            (review == "CHANGES_REQUESTED", "Changes requested", 60),
            (
                item.kind == Kind.PR and not draft and review == "REVIEW_REQUIRED",
                "Review needed",
                50,
            ),
            (item.kind == Kind.ISSUE and not issue.assignees, "Unassigned issue", 20),
            (age >= STALE_DAYS, "No item update for 30+ days", 30),
            (
                item.kind == Kind.PR and checks == "UNKNOWN",
                "Check status unavailable",
                0,
            ),
        ]
        for applies, reason, weight in rules:
            if applies:
                reasons.append(reason)
                score += weight
    return Assessment(
        disposition=disposition,
        age_days=age,
        draft=draft,
        review=review,
        checks=checks,
        mergeable=mergeable,
        signals_stale=stale,
        reasons=reasons,
        score=score,
        actionable=(
            item.state == State.OPEN
            and item.available
            and repo.present
            and not repo.archived
            and disposition not in {Disposition.SNOOZED, Disposition.DONE}
        ),
    )


def present_item(
    item: ItemSnapshot, repo: RepositoryStatus, triage: TriageSnapshot, at: datetime
) -> ItemView:
    issue = IssuePayload.model_validate(item.raw)
    assessment = assess(item, repo, triage, at)
    return ItemView(
        **item.model_dump(),
        **assessment.model_dump(),
        repo=repo.name,
        archived=repo.archived,
        present=repo.present,
        repo_error=repo.error,
        note=triage.note,
        snoozed_until=triage.snoozed_until,
        triaged_at=triage.updated_at,
        labels=[
            label if isinstance(label, str) else label.name for label in issue.labels
        ],
        assignees=[person.login for person in issue.assignees],
        author=issue.user.login if issue.user else "ghost",
    )
