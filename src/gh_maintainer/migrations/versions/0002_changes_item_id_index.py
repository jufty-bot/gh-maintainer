"""Add index on changes(item_id, id) for fast observation lookups and pruning.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "changes_item_id",
        "changes",
        ["item_id", "id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "changes_item_id",
        table_name="changes",
        if_exists=True,
    )
