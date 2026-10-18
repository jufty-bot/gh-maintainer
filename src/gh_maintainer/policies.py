"""Pure triage rules and presentation derived from validated snapshots."""

import re
from datetime import datetime

from gh_maintainer.schemas import (
    Assessment,
    Category,
    Disposition,
    IssuePayload,
    ItemSnapshot,
    ItemView,
    JsonObject,
    Kind,
    Priority,
    RepositoryStatus,
    SignalsPayload,
    State,
    TriageSnapshot,
)

STALE_DAYS = 30
SECONDS_PER_DAY = 86400


def infer_category(item: ItemSnapshot) -> Category:
    issue = IssuePayload.model_validate(item.raw)
    author = (issue.user.login if issue.user else "").lower()
    if author in {"dependabot", "dependabot[bot]", "renovate", "renovate[bot]"}:
        return Category.DEPENDENCY_UPGRADE

    labels = {
        (label if isinstance(label, str) else label.name).lower()
        for label in issue.labels
    }
    title = item.title.lower()

    if item.kind == Kind.PR:
        if (
            any(
                lbl in labels
                for lbl in (
                    "dependencies",
                    "dependency",
                    "deps",
                    "dependabot",
                    "renovate",
                )
            )
            or title.startswith(("deps:", "deps(", "build(deps"))
            or title.startswith("bump ")
        ):
            return Category.DEPENDENCY_UPGRADE

    if any(
        lbl in labels for lbl in ("security", "vulnerability", "cve")
    ) or title.startswith(("sec:", "security:", "security(")):
        return Category.SECURITY

    if any(
        lbl in labels for lbl in ("documentation", "docs", "doc")
    ) or title.startswith(("docs:", "doc:", "docs(")):
        return Category.DOCUMENTATION

    if any(
        lbl in labels for lbl in ("ci", "automation", "actions", "build", "workflows")
    ) or title.startswith(("ci:", "ci(", "build:", "build(")):
        return Category.CI

    if item.kind == Kind.ISSUE and any(
        lbl in labels for lbl in ("question", "support", "discussion", "help wanted")
    ):
        return Category.QUESTION

    if any(lbl in labels for lbl in ("bug", "defect")) or title.startswith(
        ("fix:", "fix(", "bug:", "bug(", "hotfix:", "hotfix(")
    ):
        return Category.BUG_FIX if item.kind == Kind.PR else Category.BUG_REPORT

    if any(lbl in labels for lbl in ("enhancement", "feature")) or title.startswith(
        ("feat:", "feat(", "feature:", "feature(")
    ):
        return (
            Category.FEATURE_CONTRIBUTION
            if item.kind == Kind.PR
            else Category.FEATURE_REQUEST
        )

    if any(
        lbl in labels
        for lbl in (
            "maintenance",
            "chore",
            "refactor",
            "cleanup",
            "tech debt",
            "technical debt",
            "style",
        )
    ) or title.startswith(
        ("chore:", "chore(", "refactor:", "refactor(", "style:", "style(")
    ):
        return Category.MAINTENANCE

    if item.kind == Kind.PR:
        if "fix" in title or "bug" in title:
            return Category.BUG_FIX
        if "feat" in title:
            return Category.FEATURE_CONTRIBUTION
    else:
        if "bug" in title or "error" in title or "fail" in title or "crash" in title:
            return Category.BUG_REPORT
        if "feature" in title or "request" in title:
            return Category.FEATURE_REQUEST

    return Category.OTHER


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
        and (
            item.updated_at > triage.updated_at
            or (
                triage.source_revision is not None
                and item.revision > triage.source_revision
            )
        )
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
        priority_rules = [
            (triage.priority == Priority.URGENT, "Urgent priority", 100),
            (triage.priority == Priority.HIGH, "High priority", 50),
            (triage.priority == Priority.MEDIUM, "Medium priority", 25),
        ]
        for applies, reason, weight in priority_rules:
            if applies:
                reasons.append(reason)
                score += weight
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


CLOSING_PATTERN = re.compile(
    r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+(?:https?://github\.com/[^/\s]+/[^/\s]+/issues/|(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#)(\d+)\b",
    re.IGNORECASE,
)


def extract_linked_issues(
    body: str | None, signals: JsonObject | None = None
) -> list[int]:
    results: list[int] = []
    if signals and isinstance(signals, dict):
        closing = signals.get("closingIssuesReferences")
        if isinstance(closing, dict):
            nodes = closing.get("nodes")
            if isinstance(nodes, list):
                for node in nodes:
                    if isinstance(node, dict):
                        raw_num = node.get("number")
                        if isinstance(raw_num, int | str):
                            try:
                                num = int(raw_num)
                                if num not in results:
                                    results.append(num)
                            except (ValueError, TypeError):
                                pass
    if body:
        for match in CLOSING_PATTERN.finditer(body):
            num = int(match.group(1))
            if num not in results:
                results.append(num)
    return results


def present_item(
    item: ItemSnapshot, repo: RepositoryStatus, triage: TriageSnapshot, at: datetime
) -> ItemView:
    issue = IssuePayload.model_validate(item.raw)
    assessment = assess(item, repo, triage, at)
    category = triage.category or infer_category(item)
    triaged = bool(
        triage.updated_at
        and item.updated_at <= triage.updated_at
        and (triage.source_revision is None or item.revision <= triage.source_revision)
        and (
            triage.disposition != Disposition.INBOX
            or triage.priority is not None
            or triage.category is not None
            or bool(triage.note)
        )
    )
    linked_issues: list[int] = []
    if item.kind == Kind.PR:
        linked_issues = extract_linked_issues(issue.body, item.signals)
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
        priority=triage.priority,
        category=category,
        triaged=triaged,
        linked_issues=linked_issues,
    )
