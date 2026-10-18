"""Typed ORM records; GitHub SDK objects never enter the database layer."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from gh_maintainer.clock import parse_date, timestamp
from gh_maintainer.schemas import JsonObject


class UTCDateTime(TypeDecorator[datetime]):
    """Canonical UTC text preserves compatibility with the original SQLite index."""

    impl = Text
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, _dialect: object
    ) -> str | None:
        return timestamp(value) if value is not None else None

    def process_result_value(
        self, value: str | None, _dialect: object
    ) -> datetime | None:
        return parse_date(value) if value is not None else None


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[type[str], type[Text]]] = {str: Text}


class Setting(Base):
    __tablename__ = "metadata"
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class Owner(Base):
    __tablename__ = "owners"
    id: Mapped[str] = mapped_column(primary_key=True)
    login: Mapped[str]
    kind: Mapped[str]


class RepositoryOwner(Base):
    __tablename__ = "repo_owners"
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"), index=True)


class Repository(Base):
    __tablename__ = "repos"
    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    url: Mapped[str]
    archived: Mapped[bool] = mapped_column(Boolean)
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    raw: Mapped[JsonObject] = mapped_column(JSON)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reconciled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    signals_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None]
    comments_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    activity: Mapped[JsonObject | None] = mapped_column(JSON(none_as_null=True))
    activity_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    activity_error: Mapped[str | None]


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("repo_id", "number"),
        Index("items_queue", "state", "kind", "updated_at"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id"))
    number: Mapped[int]
    kind: Mapped[str]
    state: Mapped[str]
    title: Mapped[str]
    url: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    raw: Mapped[JsonObject] = mapped_column(JSON)
    pr: Mapped[JsonObject | None] = mapped_column(JSON(none_as_null=True))
    signals: Mapped[JsonObject | None] = mapped_column(JSON(none_as_null=True))
    signals_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revision: Mapped[int] = mapped_column(default=1, server_default="1")


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        ForeignKeyConstraint(["repo_id", "number"], ["items.repo_id", "items.number"]),
        Index("comments_item", "repo_id", "number", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    repo_id: Mapped[str]
    number: Mapped[int]
    kind: Mapped[str]
    body: Mapped[str]
    author: Mapped[str | None]
    url: Mapped[str]
    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    available: Mapped[bool] = mapped_column(Boolean)
    raw: Mapped[JsonObject] = mapped_column(JSON)


class Observation(Base):
    __tablename__ = "changes"
    __table_args__ = (Index("changes_item_id", "item_id", "id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str]
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    before_json: Mapped[JsonObject | None] = mapped_column(JSON(none_as_null=True))
    after_json: Mapped[JsonObject] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(default="updated", server_default="updated")
    revision: Mapped[int | None]


class TriageDecision(Base):
    __tablename__ = "triage"
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    disposition: Mapped[str]
    note: Mapped[str] = mapped_column(default="")
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    source_revision: Mapped[int | None]


class Processing(Base):
    __tablename__ = "processing"
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    revision: Mapped[int]
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime)
    actor: Mapped[str]


class AnalysisRecord:
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    source_revision: Mapped[int]
    producer: Mapped[str]
    reason: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Summary(AnalysisRecord, Base):
    __tablename__ = "summaries"
    text: Mapped[str]


class Classification(AnalysisRecord, Base):
    __tablename__ = "classifications"
    labels: Mapped[list[str]] = mapped_column(JSON)


class Ranking(AnalysisRecord, Base):
    __tablename__ = "rankings"
    score: Mapped[float] = mapped_column(Float)


class SyncRun(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str]
    error: Mapped[str | None]
    source_calls: Mapped[int] = mapped_column("requests", default=0)
