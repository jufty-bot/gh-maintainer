"""Store conversations, analysis, source revisions, and processing checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repos", sa.Column("comments_synced_at", sa.Text))
    op.add_column(
        "items", sa.Column("revision", sa.Integer, nullable=False, server_default="1")
    )
    op.add_column(
        "changes", sa.Column("kind", sa.Text, nullable=False, server_default="updated")
    )
    op.add_column("changes", sa.Column("revision", sa.Integer))
    op.add_column("triage", sa.Column("source_revision", sa.Integer))
    op.create_table(
        "comments",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("repo_id", sa.Text, nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("author", sa.Text),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text),
        sa.Column("updated_at", sa.Text),
        sa.Column("available", sa.Boolean, nullable=False),
        sa.Column("raw", sa.JSON, nullable=False),
        sa.ForeignKeyConstraint(
            ["repo_id", "number"], ["items.repo_id", "items.number"]
        ),
    )
    op.create_index(
        "comments_item", "comments", ["repo_id", "number", "created_at", "id"]
    )
    op.create_table(
        "processing",
        sa.Column("item_id", sa.Text, sa.ForeignKey("items.id"), primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("checked_at", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
    )
    for table, value in (
        ("summaries", sa.Column("text", sa.Text, nullable=False)),
        ("classifications", sa.Column("labels", sa.JSON, nullable=False)),
        ("rankings", sa.Column("score", sa.Float, nullable=False)),
    ):
        op.create_table(
            table,
            sa.Column("item_id", sa.Text, sa.ForeignKey("items.id"), primary_key=True),
            sa.Column("source_revision", sa.Integer, nullable=False),
            sa.Column("producer", sa.Text, nullable=False),
            sa.Column("reason", sa.Text, nullable=False),
            sa.Column("updated_at", sa.Text, nullable=False),
            value,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrading would discard conversations and maintenance decisions."
    )
