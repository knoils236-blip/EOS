"""Add persisted task execution attempts.

Existing tasks get no attempts: their history before this revision is unknown and is left that way.
"""

from alembic import op
import sqlalchemy as sa

revision = "e7a3c91d5b20"
down_revision = "c4e92b6a10d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "protocol_run_name",
            sa.String(255),
            sa.ForeignKey("protocol_runs.name", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("task_name", sa.String(255), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("retry", sa.String(8), nullable=False),
        sa.Column("devices", sa.JSON(), nullable=False),
        sa.Column("external_request_id", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stop_confirmed", sa.Boolean(), nullable=True),
        sa.Column("device_available", sa.Boolean(), nullable=True),
        sa.Column("needs_manual_recovery", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.UniqueConstraint("protocol_run_name", "task_name", "attempt_number", name="uq_task_attempt"),
    )
    op.create_index(op.f("ix_task_attempts_protocol_run_name"), "task_attempts", ["protocol_run_name"], unique=False)
    op.create_index(op.f("ix_task_attempts_outcome"), "task_attempts", ["outcome"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_task_attempts_outcome"), table_name="task_attempts")
    op.drop_index(op.f("ix_task_attempts_protocol_run_name"), table_name="task_attempts")
    op.drop_table("task_attempts")
