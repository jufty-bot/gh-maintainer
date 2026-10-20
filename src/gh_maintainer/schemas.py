"""Validated source snapshots and explicit application/API contracts."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
)

JsonObject = dict[str, JsonValue]


def source_content(raw: JsonObject) -> JsonObject:
    """Compare source content without embedded account/repository profile metadata.

    Unknown source fields remain significant. Raw documents are always retained;
    only known embedded profiles are reduced to their source identities.
    """
    result = raw.copy()
    actor_fields = {"id", "node_id", "login", "type"}
    for key in ("user", "assignee", "merged_by", "closed_by", "editor"):
        value = result.get(key)
        if isinstance(value, dict):
            result[key] = {k: v for k, v in value.items() if k in actor_fields}
    for key in ("assignees", "requested_reviewers"):
        values = result.get(key)
        if isinstance(values, list):
            result[key] = [
                {k: v for k, v in value.items() if k in actor_fields}
                if isinstance(value, dict)
                else value
                for value in values
            ]
    for key in ("head", "base"):
        value = result.get(key)
        if isinstance(value, dict):
            ref = source_content(value)
            repo = ref.get("repo")
            if isinstance(repo, dict):
                ref["repo"] = {
                    k: v for k, v in repo.items() if k in {"id", "node_id", "full_name"}
                }
            result[key] = ref
    return result


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Kind(str, Enum):
    ISSUE = "issue"
    PR = "pr"


class State(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class Disposition(str, Enum):
    INBOX = "inbox"
    PLANNED = "planned"
    WAITING = "waiting"
    SNOOZED = "snoozed"
    DONE = "done"


class Priority(str, Enum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(str, Enum):
    FEATURE_REQUEST = "feature_request"
    FEATURE_CONTRIBUTION = "feature_contribution"
    BUG_REPORT = "bug_report"
    BUG_FIX = "bug_fix"
    DEPENDENCY_UPGRADE = "dependency_upgrade"
    DOCUMENTATION = "documentation"
    MAINTENANCE = "maintenance"
    CI = "ci"
    QUESTION = "question"
    SECURITY = "security"
    OTHER = "other"


class ItemSort(str, Enum):
    PRIORITY = "priority"
    UPDATED = "updated"
    OLDEST = "oldest"
    RANK = "rank"


class ItemFocus(str, Enum):
    ATTENTION = "attention"
    HIGH = "high"
    STALE = "stale"


class Person(BaseModel):
    login: str = Field(min_length=1)


class OwnerPayload(Person):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str = Field(min_length=1)
    type: str = "User"


def owner_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", value):
        raise ValueError("Expected a GitHub user or organization login.")
    return value.lower()


def repository_name(value: str) -> str:
    parts = value.split("/")
    if (
        len(parts) != 2  # noqa: PLR2004
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[-1])
        or parts[-1] in {".", ".."}
    ):
        raise ValueError("Expected a repository name in owner/repository form.")
    owner_name(parts[0])
    return value.lower()


class Label(BaseModel):
    name: str


class RepositoryPayload(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str = Field(min_length=1)
    full_name: str = Field(min_length=1)
    html_url: str = Field(min_length=1)
    owner: OwnerPayload
    archived: bool = False


class IssuePayload(BaseModel):
    node_id: str = Field(min_length=1)
    number: int = Field(gt=0)
    state: Literal["open", "closed"]
    title: str = Field(min_length=1)
    html_url: str = Field(min_length=1)
    updated_at: AwareDatetime
    pull_request: JsonObject | None = None
    labels: list[Label | str] = Field(default_factory=list)
    assignees: list[Person] = Field(default_factory=list)
    user: Person | None = None
    body: str | None = None


class Head(BaseModel):
    sha: str | None = None


class PullPayload(BaseModel):
    node_id: str = Field(min_length=1)
    state: Literal["open", "closed"]
    updated_at: AwareDatetime
    merged_at: AwareDatetime | None = None
    draft: bool = False
    head: Head = Field(default_factory=Head)


class CheckRollup(BaseModel):
    state: str | None = None


class Commit(BaseModel):
    oid: str | None = None
    statusCheckRollup: CheckRollup | None = None  # noqa: N815 - GitHub field


class CommitNode(BaseModel):
    commit: Commit


class Commits(BaseModel):
    nodes: list[CommitNode] = Field(default_factory=list)


class SignalsPayload(BaseModel):
    id: str | None = None
    reviewDecision: str | None = None  # noqa: N815 - GitHub field
    mergeable: str = "UNKNOWN"
    mergeStateStatus: str | None = None  # noqa: N815 - GitHub field
    commits: Commits = Field(default_factory=Commits)


class AccountSnapshot(Contract):
    id: str = Field(min_length=1)
    login: str = Field(min_length=1)
    kind: Literal["User", "Organization"] = "User"


class RepositorySnapshot(Contract):
    id: str
    name: str
    url: str
    archived: bool = False
    raw: JsonObject

    @classmethod
    def from_github(cls, raw: JsonObject) -> RepositorySnapshot:
        data = RepositoryPayload.model_validate(raw)
        return cls(
            id=data.id,
            name=data.full_name,
            url=data.html_url,
            archived=data.archived,
            raw=raw,
        )


class ItemSnapshot(Contract):
    id: str
    repo_id: str
    number: int = Field(gt=0)
    kind: Kind
    state: State
    title: str
    url: str
    updated_at: AwareDatetime
    available: bool = True
    raw: JsonObject
    pr: JsonObject | None = None
    signals: JsonObject | None = None
    signals_at: AwareDatetime | None = None
    revision: int = Field(default=1, ge=1)

    @classmethod
    def from_github(
        cls, raw: JsonObject, repo_id: str, previous: ItemSnapshot | None = None
    ) -> ItemSnapshot:
        data = IssuePayload.model_validate(raw)
        return cls(
            id=data.node_id,
            repo_id=repo_id,
            number=data.number,
            kind=Kind.PR if data.pull_request is not None else Kind.ISSUE,
            state=State(data.state),
            title=data.title,
            url=data.html_url,
            updated_at=data.updated_at,
            raw=raw,
            pr=previous.pr if previous else None,
            signals=previous.signals if previous else None,
            signals_at=previous.signals_at if previous else None,
        )

    def with_pull(self, raw: JsonObject) -> ItemSnapshot:
        data = PullPayload.model_validate(raw)
        return self.model_copy(
            update={
                "pr": raw,
                "state": State.MERGED if data.merged_at else State(data.state),
                "updated_at": max(self.updated_at, data.updated_at),
            }
        )


class RepositoryCommit(Contract):
    sha: str = Field(min_length=1, validation_alias=AliasChoices("sha", "oid"))
    message: str = Field(validation_alias=AliasChoices("message", "messageHeadline"))
    committed_at: AwareDatetime = Field(
        validation_alias=AliasChoices("committed_at", "committedDate")
    )
    url: str = Field(min_length=1)


class RepositoryRelease(Contract):
    id: str = Field(min_length=1)
    name: str | None
    tag_name: str = Field(
        min_length=1, validation_alias=AliasChoices("tag_name", "tagName")
    )
    published_at: AwareDatetime = Field(
        validation_alias=AliasChoices("published_at", "publishedAt")
    )
    url: str = Field(min_length=1)


class RepositoryItem(Contract):
    id: str = Field(min_length=1)
    number: int = Field(gt=0)
    title: str
    state: State
    created_at: AwareDatetime = Field(
        validation_alias=AliasChoices("created_at", "createdAt")
    )
    updated_at: AwareDatetime = Field(
        validation_alias=AliasChoices("updated_at", "updatedAt")
    )
    url: str = Field(min_length=1)

    @field_validator("state", mode="before")
    @classmethod
    def normalize_state(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value


class RepositoryActivity(Contract):
    default_branch: str | None
    last_commit: RepositoryCommit | None
    last_release: RepositoryRelease | None
    last_pr: RepositoryItem | None
    last_issue: RepositoryItem | None
    raw: JsonObject

    @classmethod
    def from_github(cls, raw: JsonObject, repo_id: str) -> RepositoryActivity:
        # Required nullable keys distinguish an empty repository from a partial
        # response. Never turn missing connections or fields into known absence.
        if str(raw.get("databaseId")) != repo_id:
            raise ValueError(
                "GitHub returned an unexpected repository activity identity."
            )
        branch = raw["defaultBranchRef"]
        if branch is not None and not isinstance(branch, dict):
            raise ValueError("GitHub returned invalid default branch activity.")
        default_branch = branch["name"] if branch is not None else None
        commit = (
            RepositoryCommit.model_validate(branch["target"])
            if branch is not None
            else None
        )
        release = raw["latestRelease"]

        def latest(key: str) -> RepositoryItem | None:
            connection = raw[key]
            if not isinstance(connection, dict):
                raise ValueError("GitHub returned invalid repository item activity.")
            nodes = connection["nodes"]
            if not isinstance(nodes, list) or len(nodes) > 1:
                raise ValueError("GitHub returned invalid repository item activity.")
            return RepositoryItem.model_validate(nodes[0]) if nodes else None

        return cls.model_validate(
            {
                "default_branch": default_branch,
                "last_commit": commit,
                "last_release": RepositoryRelease.model_validate(release)
                if release is not None
                else None,
                "last_pr": latest("pullRequests"),
                "last_issue": latest("issues"),
                "raw": raw,
            }
        )


class RepositoryStatus(RepositorySnapshot):
    present: bool = True
    synced_at: AwareDatetime | None = None
    reconciled_at: AwareDatetime | None = None
    signals_at: AwareDatetime | None = None
    error: str | None = None
    comments_synced_at: AwareDatetime | None = None
    activity: RepositoryActivity | None = None
    activity_synced_at: AwareDatetime | None = None
    activity_error: str | None = None
    activity_stale: bool = True


class TriageSnapshot(Contract):
    disposition: Disposition = Disposition.INBOX
    note: str = ""
    priority: Priority | None = None
    category: Category | None = None
    snoozed_until: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    source_revision: int | None = None


class AnalysisInput(Contract):
    producer: str = Field(default="manual", min_length=1)
    reason: str = ""
    text: str | None = Field(default=None, min_length=1)
    labels: list[str] | None = None
    score: float | None = Field(default=None, allow_inf_nan=False)


class AnalysisView(AnalysisInput):
    source_revision: int
    updated_at: AwareDatetime
    stale: bool = False


class CommentPayload(BaseModel):
    node_id: str = Field(min_length=1)
    body: str | None = None
    user: Person | None = None
    html_url: str
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    submitted_at: AwareDatetime | None = None


class CommentSnapshot(Contract):
    id: str
    repo_id: str
    number: int = Field(gt=0)
    kind: Literal["issue", "inline", "review"]
    body: str
    author: str | None
    url: str
    created_at: AwareDatetime | None
    updated_at: AwareDatetime | None
    available: bool = True
    raw: JsonObject
    in_reply_to_id: int | None = None

    @classmethod
    def from_github(
        cls,
        raw: JsonObject,
        repo_id: str,
        number: int,
        kind: Literal["issue", "inline", "review"],
    ) -> CommentSnapshot:
        data = CommentPayload.model_validate(raw)
        reply_to = raw.get("in_reply_to_id")
        in_reply_to_id: int | None = None
        if isinstance(reply_to, int | str):
            try:
                in_reply_to_id = int(reply_to)
            except (ValueError, TypeError):
                in_reply_to_id = None
        return cls(
            id=data.node_id,
            repo_id=repo_id,
            number=number,
            kind=kind,
            body=data.body or "",
            author=data.user.login if data.user else None,
            url=data.html_url,
            created_at=data.created_at or data.submitted_at,
            updated_at=data.updated_at or data.submitted_at,
            raw=raw,
            in_reply_to_id=in_reply_to_id,
        )


class CommentPage(Contract):
    total: int
    comments_synced_at: AwareDatetime | None
    coverage_error: str | None
    comments: list[CommentSnapshot]


class TriageUpdate(Contract):
    disposition: Disposition = Disposition.PLANNED
    note: str = Field(default="", max_length=10000)
    priority: Priority | None = None
    category: Category | None = None
    snoozed_until: AwareDatetime | None = None
    revision: int | None = Field(default=None, ge=1)
    finish_review: bool = False
    actor: str = Field(default="dashboard", min_length=1)


class Assessment(Contract):
    disposition: Disposition
    age_days: int
    draft: bool
    review: str
    checks: str
    mergeable: str
    signals_stale: bool
    reasons: list[str]
    score: int
    actionable: bool


class FollowUpSummary(Contract):
    baseline: Literal["review", "decision"]
    since_revision: int
    since_at: AwareDatetime
    kinds: list[str] = Field(default_factory=list)
    history_gap: bool = False
    decision_at_review: bool | None = None


class ItemView(ItemSnapshot, Assessment):
    repo: str
    archived: bool
    present: bool
    repo_error: str | None
    note: str | None
    snoozed_until: AwareDatetime | None
    triaged_at: AwareDatetime | None
    labels: list[str]
    assignees: list[str]
    author: str
    priority: Priority | None = None
    category: Category = Category.OTHER
    triaged: bool = False
    processing_state: Literal["unprocessed", "changed", "processed"] = "unprocessed"
    processed_revision: int | None = None
    processed_by: str | None = None
    checked_at: AwareDatetime | None = None
    comments_synced_at: AwareDatetime | None = None
    saved_decision: TriageSnapshot | None = None
    follow_up: FollowUpSummary | None = None
    summary: AnalysisView | None = None
    classification: AnalysisView | None = None
    ranking: AnalysisView | None = None
    linked_issues: list[int] = Field(default_factory=list)
    linked_prs: list[int] = Field(default_factory=list)


class RepositoryView(RepositoryStatus):
    open_issues: int = 0
    open_prs: int = 0
    attention: int = 0


class RunView(Contract):
    id: int
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    status: str
    error: str | None
    source_calls: int


class Counts(Contract):
    repositories: int = 0
    indexed_items: int = 0
    open_issues: int = 0
    open_prs: int = 0
    attention: int = 0
    incomplete_repositories: int = 0


class Overview(Contract):
    owner: str | None
    owners: list[AccountSnapshot] = Field(default_factory=list)
    generated_at: AwareDatetime
    latest_run: RunView | None
    counts: Counts
    repos: list[RepositoryView]


class Portfolio(Overview):
    items: list[ItemView]


class ItemPage(Contract):
    total: int
    items: list[ItemView]


class ItemFilters(Contract):
    q: str = ""
    kind: Kind | None = None
    state: State | None = None
    repo: str = ""
    queue: bool = False
    disposition: Disposition | None = None
    processing: Literal["unprocessed", "changed", "processed", "pending"] | None = None
    classification: str | None = None
    sort: ItemSort = ItemSort.PRIORITY
    focus: ItemFocus | None = None
    author: str = ""
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    priority: Priority | None = None
    category: Category | None = None
    triaged: bool | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ObservationView(Contract):
    id: int
    item_id: str
    observed_at: AwareDatetime
    before_json: JsonObject | None
    after_json: JsonObject
    kind: str = "updated"
    revision: int | None = None


class ChangePage(Contract):
    total: int
    changes: list[ObservationView]


class ProcessingView(Contract):
    item_id: str
    revision: int
    checked_at: AwareDatetime
    actor: str = Field(min_length=1)


class ActivityEntry(Contract):
    id: int
    observed_at: AwareDatetime
    kind: str
    label: str
    detail: str
    url: str


class ActivityPage(Contract):
    total: int
    entries: list[ActivityEntry]


class ItemDetail(Contract):
    item: ItemView
    changes: list[ObservationView]
    follow_up_changes: ActivityPage | None = None


class SyncResult(Contract):
    owner: str | None
    scope: Literal["owner", "repositories"] = "owner"
    coverage: Literal["explicit", "credential-visible", "public-only"] = (
        "credential-visible"
    )
    discovered: int
    synced: int
    status: Literal["complete", "partial"]
    errors: list[str]
    source_calls: int
