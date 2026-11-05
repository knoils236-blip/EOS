"""Add idempotent consumable inventory events."""

from alembic import op
import sqlalchemy as sa

revision = "c4e92b6a10d8"
down_revision = "8c1d4e2a9f30"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_events",
        sa.Column("resource_name", sa.String(255), primary_key=True),
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("inventory_events")
