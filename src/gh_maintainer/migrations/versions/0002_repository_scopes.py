"""Support multiple owners without rewriting legacy source or triage records."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("login", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
    )
    op.create_table(
        "repo_owners",
        sa.Column("repo_id", sa.Text, sa.ForeignKey("repos.id"), primary_key=True),
        sa.Column("owner_id", sa.Text, sa.ForeignKey("owners.id"), nullable=False),
    )
    op.create_index("ix_repo_owners_owner_id", "repo_owners", ["owner_id"])
    # Keep the legacy metadata intact; associate its inventory with the stable owner ID.
    op.execute("""
        INSERT INTO owners(id, login, kind)
        SELECT identity.value, login.value, 'User'
        FROM metadata AS identity JOIN metadata AS login ON login.key = 'owner'
        WHERE identity.key = 'owner_id'
    """)
    op.execute("""
        INSERT OR IGNORE INTO owners(id, login, kind)
        SELECT CAST(json_extract(raw, '$.owner.id') AS TEXT),
               json_extract(raw, '$.owner.login'),
               CASE WHEN json_extract(raw, '$.owner.type') = 'Organization'
                    THEN 'Organization' ELSE 'User' END
        FROM repos WHERE json_extract(raw, '$.owner.id') IS NOT NULL
    """)
    op.execute("""
        INSERT INTO repo_owners(repo_id, owner_id)
        SELECT repos.id, owners.id FROM repos JOIN owners
        ON CAST(json_extract(repos.raw, '$.owner.id') AS TEXT) = owners.id
        OR (json_extract(repos.raw, '$.owner.id') IS NULL
            AND lower(json_extract(repos.raw, '$.owner.login')) = lower(owners.login))
    """)


def downgrade() -> None:
    op.drop_table("repo_owners")
    op.drop_table("owners")
