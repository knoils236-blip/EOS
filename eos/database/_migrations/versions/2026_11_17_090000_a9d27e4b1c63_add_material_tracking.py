"""Add containers, samples and idempotent material events.

Earlier data has no physical sample identities; nothing is inferred from file or run names.
"""

from alembic import op
import sqlalchemy as sa

revision = "a9d27e4b1c63"
down_revision = "f1b84d6c2e57"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "material_containers",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("type", sa.String(255), nullable=False),
        sa.Column("capacity_ul", sa.Float(), nullable=True),
        sa.Column("position", sa.String(255), nullable=True, unique=True),
        sa.Column("position_known", sa.Boolean(), nullable=False),
        sa.Column("reserved_position", sa.String(255), nullable=True, unique=True),
    )
    op.create_table(
        "material_samples",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("container_id", sa.String(255), sa.ForeignKey("material_containers.id"), nullable=True),
        sa.Column("parent_id", sa.String(255), sa.ForeignKey("material_samples.id"), nullable=True),
        sa.Column("volume_ul", sa.Float(), nullable=True),
        sa.Column("removed", sa.Boolean(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_table(
        "material_events",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("material_events")
    op.drop_table("material_samples")
    op.drop_table("material_containers")
