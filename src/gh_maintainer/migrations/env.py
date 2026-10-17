"""Alembic works both through the application and the developer CLI."""

import asyncio
import os

from alembic import context
from alembic.runtime.migration import MigrationContext
from sqlalchemy import JSON, Boolean, Column, Connection, Integer, Text
from sqlalchemy.types import TypeEngine

from gh_maintainer.database import Database
from gh_maintainer.models import Base


def compare_storage_type(
    migration: MigrationContext,
    _inspected_column: Column[object],
    _model_column: Column[object],
    inspected: TypeEngine[object],
    model: TypeEngine[object],
) -> bool | None:
    # The legacy index stored JSON as TEXT and booleans as INTEGER. SQLite
    # already stores the same values; typed codecs need no table copy.
    if migration.dialect.name == "sqlite" and (
        (isinstance(inspected, Text) and isinstance(model, JSON))
        or (isinstance(inspected, Integer) and isinstance(model, Boolean))
    ):
        return False
    return None


def include_schema_object(
    object_: object,
    _name: str | None,
    kind: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    # SQLite reports old PRIMARY KEY columns as nullable when their DDL omitted
    # explicit NOT NULL. Keep that declaration without rebuilding every table.
    # Changed/missing keys and changes to their types remain visible.
    return not (
        kind == "column"
        and not reflected
        and isinstance(object_, Column)
        and isinstance(compare_to, Column)
        and object_.primary_key
        and compare_to.primary_key
        and not object_.nullable
        and compare_to.nullable
        and str(object_.type) == str(compare_to.type)
    )


def run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
        compare_type=compare_storage_type,
        include_object=include_schema_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_cli() -> None:
    database = Database(os.getenv("GH_MAINTAINER_DB", ".data/maintainer.sqlite"))
    try:
        async with database.engine.connect() as connection:
            await connection.execution_options(migration=True)
            async with connection.begin():
                await connection.run_sync(run)
    finally:
        await database.close()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    asyncio.run(run_cli())
