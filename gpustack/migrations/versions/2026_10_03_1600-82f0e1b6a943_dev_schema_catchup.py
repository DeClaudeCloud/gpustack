"""Catch up schemas on databases that already applied the dev bundles.

Revision ID: 82f0e1b6a943
Revises: 6d4092d2e980
Create Date: 2026-10-03 16:00:00.000000

Dev bundle revisions can gain schema objects after a database has applied
them. A new revision is needed to deliver those objects to existing installs.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from gpustack.migrations.utils import column_exists, table_exists
from gpustack.schemas.common import UTCDateTime


revision: str = '82f0e1b6a943'
down_revision: Union[str, None] = '6d4092d2e980'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not column_exists('models', 'revision_history_limit'):
        op.add_column('models', sa.Column(
            'revision_history_limit', sa.Integer(), nullable=False, server_default='10'
        ))
    if not table_exists('model_revisions'):
        op.create_table(
            'model_revisions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('model_id', sa.Integer(), sa.ForeignKey('models.id', ondelete='CASCADE'), nullable=False),
            sa.Column('revision', sa.Integer(), nullable=False),
            sa.Column('spec', sa.JSON(), nullable=False),
            sa.Column('created_at', UTCDateTime(), nullable=False),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('principals.id', ondelete='SET NULL'), nullable=True),
            sa.UniqueConstraint('model_id', 'revision'),
        )
    if not column_exists('cache_service_instances', 'computed_resource_claim'):
        op.add_column('cache_service_instances', sa.Column(
            'computed_resource_claim', sa.JSON(), nullable=True
        ))


def downgrade() -> None:
    # The upstream bundle revisions own these objects and remove them on
    # downgrade. Keeping them here preserves history and makes downgrade
    # independent of whether upgrade found them already present.
    pass
