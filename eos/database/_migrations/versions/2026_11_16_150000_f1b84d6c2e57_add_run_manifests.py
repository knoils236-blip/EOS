"""Add run manifests and bind protocol runs and approval requests to them.

Existing runs and requests keep a NULL manifest_hash: their plan was never recorded, and today's definitions are not
filled in as if it had been.
"""

from alembic import op
import sqlalchemy as sa

revision = "f1b84d6c2e57"
down_revision = "e7a3c91d5b20"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "run_manifests",
        sa.Column("hash", sa.String(64), primary_key=True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("protocol_runs", sa.Column("manifest_hash", sa.String(64), nullable=True))
    op.add_column("approval_requests", sa.Column("manifest_hash", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("approval_requests", "manifest_hash")
    op.drop_column("protocol_runs", "manifest_hash")
    op.drop_table("run_manifests")
