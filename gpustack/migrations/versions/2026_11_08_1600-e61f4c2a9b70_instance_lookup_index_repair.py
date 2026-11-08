"""Ensure applied v2.3.1 databases have instance membership lookup indexes.

Revision ID: e61f4c2a9b70
Revises: c30524955077
Create Date: 2026-11-08 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from gpustack.migrations.utils import table_exists


revision: str = 'e61f4c2a9b70'
down_revision: Union[str, None] = 'c30524955077'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists('model_instances'):
        return
    indexes = sa.inspect(op.get_bind()).get_indexes('model_instances')
    for field in ('cluster_id', 'model_id'):
        # Preserve supporting FK indexes on MySQL and OceanBase, including
        # composite indexes whose leading column already serves this lookup.
        if not any(ix['column_names'][:1] == [field] for ix in indexes):
            op.create_index(f'ix_model_instances_{field}', 'model_instances', [field])


def downgrade() -> None:
    # The v2.3.1 bundle owns these indexes and removes them on downgrade.
    # Keep them here regardless of whether the repair found them already present.
    pass
