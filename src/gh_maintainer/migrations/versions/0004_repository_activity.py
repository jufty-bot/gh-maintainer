"""Retain repository commit, release, PR and issue activity with separate coverage."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repos", sa.Column("activity", sa.JSON))
    op.add_column("repos", sa.Column("activity_synced_at", sa.Text))
    op.add_column("repos", sa.Column("activity_error", sa.Text))


def downgrade() -> None:
    raise RuntimeError("Downgrading would discard repository activity snapshots.")
