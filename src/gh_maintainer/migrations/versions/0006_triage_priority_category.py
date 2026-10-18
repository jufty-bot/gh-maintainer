"""Add priority and category to triage decisions

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("triage") as batch_op:
        batch_op.add_column(sa.Column("priority", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("category", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("triage") as batch_op:
        batch_op.drop_column("category")
        batch_op.drop_column("priority")
