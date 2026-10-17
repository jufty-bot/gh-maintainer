"""Explicit, versioned repository policy; no implicit working-directory discovery."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

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
