"""Explicit CLI defaults and versioned repository policies."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gh_maintainer.clock import parse_date
from gh_maintainer.schemas import Contract, Disposition, repository_name

Action = Literal["summarize", "classify", "rank", "triage", "mark-processed"]


def default_permissions() -> set[Action]:
    return {"summarize", "mark-processed"}


class RankBand(Contract):
    score: float = Field(allow_inf_nan=False)
    meaning: str = Field(min_length=1)


class RepositoryPolicy(Contract):
    permissions: set[Action] = Field(default_factory=default_permissions)
    labels: dict[str, str] = Field(default_factory=dict)
    ranking: list[RankBand] = Field(default_factory=list)
    triage: dict[Disposition, str] = Field(default_factory=dict)
    item_budget: int = Field(default=10, ge=1, le=500)
    refresh: bool = False
    test_commands: list[str] = Field(default_factory=list)

    @field_validator("labels", "triage")
    @classmethod
    def meaningful_rules(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not key.strip() or not meaning.strip() for key, meaning in value.items()
        ):
            raise ValueError("Policy labels and dispositions need names and meanings.")
        return value


class PolicyFile(Contract):
    version: Literal[1] = 1
    repositories: dict[str, RepositoryPolicy]

    @field_validator("repositories")
    @classmethod
    def normalize(
        cls, value: dict[str, RepositoryPolicy]
    ) -> dict[str, RepositoryPolicy]:
        normalized = {repository_name(key): policy for key, policy in value.items()}
        if len(normalized) != len(value):
            raise ValueError("Duplicate repository names in policy.")
        return normalized


class SelectedPolicy(Contract):
    path: str
    sha256: str
    repo: str
    policy: RepositoryPolicy


def load_policy(path: Path, repo: str) -> SelectedPolicy:
    data = path.read_bytes()
    policy_file = PolicyFile.model_validate_json(data)
    repo = repository_name(repo)
    if repo not in policy_file.repositories:
        message = f"No policy for {repo} in {path}."
        raise ValueError(message)
    return SelectedPolicy(
        path=str(path.resolve()),
        sha256=hashlib.sha256(data).hexdigest(),
        repo=repo,
        policy=policy_file.repositories[repo],
    )


def check_policy(
    policy: RepositoryPolicy,
    action: str,
    *,
    labels: tuple[str, ...] = (),
    score: float | None = None,
    disposition: str | None = None,
) -> None:
    if action not in policy.permissions:
        message = f"Repository policy does not permit {action}."
        raise ValueError(message)
    if action == "classify" and any(label not in policy.labels for label in labels):
        raise ValueError("Classification is outside the policy label vocabulary.")
    if action == "rank" and score not in {band.score for band in policy.ranking}:
        raise ValueError("Rank must match a score defined in repository policy.")
    if action == "triage" and disposition not in policy.triage:
        raise ValueError("Disposition is not defined in repository policy.")


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SyncOptions(Options):
    concurrency: int = Field(default=4, ge=1)
    requests_per_second: float = Field(default=2, gt=0, allow_inf_nan=False)
    owner: str | None = None
    full: bool = Field(default=False, description="Reconcile all historical items.")
    repo: list[str] | None = Field(
        default=None, description="Limit item sync to these owner/repository names."
    )


class WatchOptions(SyncOptions):
    interval: int = Field(default=900, ge=60, description="Seconds between syncs.")


class ReportOptions(Options):
    format: Literal["markdown", "json"] = "markdown"
    output: Path | None = Field(
        default=None, strict=False, description="Output file; otherwise use stdout."
    )


class ServeOptions(Options):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)


class OutputOptions(Options):
    format: Literal["text", "json"] = "text"


class ItemOptions(OutputOptions):
    item_id: str | None = None


class WriteOptions(ItemOptions):
    policy: Path | None = Field(default=None, strict=False)
    revision: int | None = Field(default=None, ge=1)


class AnalysisOptions(WriteOptions):
    producer: str = "manual"
    reason: str = ""


class ClassifyOptions(AnalysisOptions):
    labels: list[str] = Field(default_factory=list)


class SummarizeOptions(AnalysisOptions):
    text: str | None = None


class RankOptions(AnalysisOptions):
    score: float | None = Field(default=None, allow_inf_nan=False)


class ProcessOptions(WriteOptions):
    actor: str = "manual"


class PageOptions(OutputOptions):
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ListOptions(PageOptions):
    repo: str | None = None
    state: Literal["open", "closed", "merged", "all"] = "open"
    search: str = ""
    disposition: Literal["inbox", "planned", "waiting", "snoozed", "done"] | None = None
    processing: Literal["unprocessed", "changed", "processed", "pending"] | None = None
    unprocessed: bool = False
    classification: str | None = None
    sort: Literal["priority", "updated", "rank"] = "priority"


class StatusOptions(ListOptions):
    state: Literal["open", "closed", "merged", "all"] = "all"


class CommentsOptions(PageOptions):
    item_id: str | None = None
    include_unavailable: bool = False


class ChangesOptions(PageOptions):
    item_id: str | None = None
    since: str | None = None
    all: bool = False


class ContextOptions(ItemOptions):
    section: Literal[
        "overview", "guidance", "files", "file", "checks", "threads", "logs"
    ] = "overview"
    limit: int = Field(default=100, ge=1, le=100)
    page: int = Field(default=1, ge=1)
    cursor: str | None = None
    path: str | None = None
    ref: Literal["head", "base"] = "head"
    job_id: int | None = Field(default=None, ge=1)
    max_chars: int = Field(default=30000, ge=1, le=1000000)


class PolicyOptions(OutputOptions):
    repo: str | None = None
    policy_file: Path = Field(default=Path(".gh-maintainer.json"), strict=False)
    initialize: bool = False


class SkillsOptions(OutputOptions):
    install: Path | None = Field(default=None, strict=False)


class TriageOptions(WriteOptions):
    item_id: str | None = Field(default=None, description="GitHub issue node ID.")
    disposition: Literal["inbox", "planned", "waiting", "snoozed", "done"] | None = None
    note: str = ""
    until: str | None = Field(
        default=None,
        description="ISO timestamp with timezone, required when snoozing.",
        json_schema_extra={"format": "date-time"},
    )

    @field_validator("until", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: Any) -> Any:
        # YAML automatically decodes unquoted ISO timestamps as datetime objects.
        return value.isoformat() if isinstance(value, datetime) else value

    @field_validator("until")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        if value is not None:
            parse_date(value)
        return value


class Config(Options):
    """Shared database and command defaults; explicit CLI arguments take precedence."""

    schema_url: str | None = Field(default=None, alias="$schema")
    db: str | None = Field(
        default=None,
        description="Database path; falls back to GH_MAINTAINER_DB, then the default.",
    )
    repositories: list[str] | None = Field(
        default=None,
        description="Default owner/repository names for both sync and watch.",
    )
    sync: SyncOptions = Field(default_factory=SyncOptions)
    watch: WatchOptions = Field(default_factory=WatchOptions)
    report: ReportOptions = Field(default_factory=ReportOptions)
    serve: ServeOptions = Field(default_factory=ServeOptions)
    triage: TriageOptions = Field(default_factory=TriageOptions)
    prs: ListOptions = Field(default_factory=ListOptions)
    issues: ListOptions = Field(default_factory=ListOptions)
    status: StatusOptions = Field(default_factory=StatusOptions)
    show: ItemOptions = Field(default_factory=ItemOptions)
    comments: CommentsOptions = Field(default_factory=CommentsOptions)
    changes: ChangesOptions = Field(default_factory=ChangesOptions)
    repos: OutputOptions = Field(default_factory=OutputOptions)
    classify: ClassifyOptions = Field(default_factory=ClassifyOptions)
    summarize: SummarizeOptions = Field(default_factory=SummarizeOptions)
    rank: RankOptions = Field(default_factory=RankOptions)
    mark_processed: ProcessOptions = Field(
        default_factory=ProcessOptions, alias="mark-processed"
    )
    context: ContextOptions = Field(default_factory=ContextOptions)
    policy: PolicyOptions = Field(default_factory=PolicyOptions)
    skills: SkillsOptions = Field(default_factory=SkillsOptions)

    def command_defaults(self) -> dict[str, Any]:
        """Build Click defaults without overriding omitted command options."""
        defaults = self.model_dump(mode="json", by_alias=True, exclude_unset=True)
        # The root --db option shares its name with the db command group.
        # Resolve the database separately instead of putting a string in its map.
        for key in ("db", "$schema", "repositories"):
            defaults.pop(key, None)
        for name in ("sync", "watch"):
            if self.repositories is not None:
                defaults.setdefault(name, {}).setdefault("repo", self.repositories)
        return defaults


def load_config(path: Path) -> Config:
    """Read an explicitly selected file, rejecting invalid and unknown options."""
    suffix = path.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError("Config file must have a .json, .yaml, or .yml extension.")
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content) if suffix == ".json" else yaml.safe_load(content)
        return Config.model_validate(data)
    except (OSError, ValueError, yaml.YAMLError) as error:
        message = f"Cannot load config {path}: {error}"
        raise ValueError(message) from error


def config_schema() -> dict[str, Any]:
    """Return the public JSON Schema generated from the configuration model."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://raw.githubusercontent.com/jufty-bot/gh-maintainer/"
            "main/docs/config.schema.json"
        ),
        **Config.model_json_schema(),
    }


if __name__ == "__main__":
    Path("docs/config.schema.json").write_text(
        json.dumps(config_schema(), indent=2) + "\n", encoding="utf-8"
    )
