"""add artifacts table

Revision ID: a1b2c3d4e5f6
Revises: e15a188c1e58
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e15a188c1e58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'artifacts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False, index=True),
        sa.Column('step_number', sa.Integer(), nullable=True),
        sa.Column('artifact_type', sa.String(32), nullable=False, index=True),
        sa.Column('mime_type', sa.String(64), nullable=False, server_default='application/octet-stream'),
        sa.Column('filename', sa.String(255), nullable=False, server_default=''),
        sa.Column('file_path', sa.String(512), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('data_json', sa.JSON(), nullable=True),
        sa.Column('text_content', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_artifacts_session_type', 'artifacts', ['session_id', 'artifact_type'])


def downgrade() -> None:
    op.drop_index('ix_artifacts_session_type', table_name='artifacts')
    op.drop_table('artifacts')
