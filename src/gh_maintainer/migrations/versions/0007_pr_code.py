"""Latest PR diff and current commit membership."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pr_code",
        sa.Column("item_id", sa.Text(), sa.ForeignKey("items.id"), primary_key=True),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "pr_commits",
        sa.Column("item_id", sa.Text(), sa.ForeignKey("items.id"), primary_key=True),
        sa.Column("sha", sa.Text(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("committed_at", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.UniqueConstraint("item_id", "position"),
    )


def downgrade() -> None:
    op.drop_table("pr_commits")
    op.drop_table("pr_code")
