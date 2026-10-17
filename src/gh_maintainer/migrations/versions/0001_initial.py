"""Create a fresh schema or adopt a validated legacy index without rewriting data."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EXPECTED = {
    "metadata": {"key", "value"},
    "repos": {
        "id",
        "name",
        "url",
        "archived",
        "present",
        "raw",
        "synced_at",
        "reconciled_at",
        "signals_at",
        "error",
    },
    "items": {
        "id",
        "repo_id",
        "number",
        "kind",
        "state",
        "title",
        "url",
        "updated_at",
        "available",
        "raw",
        "pr",
        "signals",
        "signals_at",
    },
    "changes": {"id", "item_id", "observed_at", "before_json", "after_json"},
    "triage": {"item_id", "disposition", "note", "snoozed_until", "updated_at"},
    "runs": {"id", "started_at", "finished_at", "status", "error", "requests"},
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    if tables:
        if tables != set(EXPECTED) or any(
            {column["name"] for column in inspector.get_columns(table)} != columns
            for table, columns in EXPECTED.items()
        ):
            raise RuntimeError("Unrecognized database schema; migration refused.")
        return
    op.create_table(
        "metadata",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )
    op.create_table(
        "repos",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("archived", sa.Boolean, nullable=False),
        sa.Column("present", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("raw", sa.JSON, nullable=False),
        sa.Column("synced_at", sa.Text),
        sa.Column("reconciled_at", sa.Text),
        sa.Column("signals_at", sa.Text),
        sa.Column("error", sa.Text),
    )
    op.create_table(
        "items",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("repo_id", sa.Text, sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.Column("available", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("raw", sa.JSON, nullable=False),
        sa.Column("pr", sa.JSON),
        sa.Column("signals", sa.JSON),
        sa.Column("signals_at", sa.Text),
        sa.UniqueConstraint("repo_id", "number"),
    )
    op.create_index("items_queue", "items", ["state", "kind", "updated_at"])
    op.create_table(
        "changes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("observed_at", sa.Text, nullable=False),
        sa.Column("before_json", sa.JSON),
        sa.Column("after_json", sa.JSON, nullable=False),
    )
    op.create_table(
        "triage",
        sa.Column("item_id", sa.Text, sa.ForeignKey("items.id"), primary_key=True),
        sa.Column("disposition", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("snoozed_until", sa.Text),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("started_at", sa.Text, nullable=False),
        sa.Column("finished_at", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("requests", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "This baseline cannot be downgraded without deleting index data."
    )
