"""Validated source snapshots and explicit application/API contracts."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

JsonObject = dict[str, JsonValue]


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


class Person(BaseModel):
    login: str = Field(min_length=1)


class Label(BaseModel):
    name: str


class RepositoryPayload(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str = Field(min_length=1)
    full_name: str = Field(min_length=1)
    html_url: str = Field(min_length=1)
    owner: Person
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


class RepositoryStatus(RepositorySnapshot):
    present: bool = True
    synced_at: AwareDatetime | None = None
    reconciled_at: AwareDatetime | None = None
    signals_at: AwareDatetime | None = None
    error: str | None = None


class TriageSnapshot(Contract):
    disposition: Disposition = Disposition.INBOX
    note: str = ""
    snoozed_until: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None


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
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ObservationView(Contract):
    id: int
    item_id: str
    observed_at: AwareDatetime
    before_json: JsonObject | None
    after_json: JsonObject


class ItemDetail(Contract):
    item: ItemView
    changes: list[ObservationView]


class SyncResult(Contract):
    owner: str
    discovered: int
    synced: int
    status: Literal["complete", "partial"]
    errors: list[str]
    source_calls: int
