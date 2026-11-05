"""Add lab notebook entries and approval requests

Revision ID: 8c1d4e2a9f30
Revises: 5b8e2f1c7a94
Create Date: 2026-11-03 16:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = '8c1d4e2a9f30'
down_revision = '5b8e2f1c7a94'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('notebook_entries',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('campaign_name', sa.String(length=255), nullable=True),
    sa.Column('protocol_run_name', sa.String(length=255), nullable=True),
    sa.Column('author', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notebook_entries_campaign_name'), 'notebook_entries', ['campaign_name'], unique=False)
    op.create_index(op.f('ix_notebook_entries_protocol_run_name'), 'notebook_entries', ['protocol_run_name'], unique=False)
    op.create_table('approval_requests',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('labs', sa.JSON(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('requested_by', sa.String(length=255), nullable=False),
    sa.Column('decided_by', sa.String(length=255), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approval_requests_status'), 'approval_requests', ['status'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_approval_requests_status'), table_name='approval_requests')
    op.drop_table('approval_requests')
    op.drop_index(op.f('ix_notebook_entries_protocol_run_name'), table_name='notebook_entries')
    op.drop_index(op.f('ix_notebook_entries_campaign_name'), table_name='notebook_entries')
    op.drop_table('notebook_entries')
