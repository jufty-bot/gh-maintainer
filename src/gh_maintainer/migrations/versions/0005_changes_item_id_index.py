"""Add index on changes(item_id, id) for fast observation lookups and pruning.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""

from alembic import op

revision = "0005"
down_revision = "0004"
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
