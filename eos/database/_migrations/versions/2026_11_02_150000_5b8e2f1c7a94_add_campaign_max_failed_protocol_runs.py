"""Add campaign max_failed_protocol_runs

Revision ID: 5b8e2f1c7a94
Revises: 361d200c0292
Create Date: 2026-11-02 15:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers used by Alembic.
revision = "5b8e2f1c7a94"
down_revision = "361d200c0292"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campaigns",
        sa.Column("max_failed_protocol_runs", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("campaigns", "max_failed_protocol_runs")
