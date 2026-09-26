"""usage details: user agent

A ``user_agent`` column on ``model_usage_details`` and its archive, for the
request log. It is filled from the request's User-Agent header by the direct
(non-gateway) inference path, from a gateway report that carries one, and --
in ``embedded`` gateway mode -- from the gateway's access log (see
``gpustack.gateway.access_log``).

Nullable with no backfill: rows written before this revision have no user
agent on record.

Revision ID: 6d4092d2e980
Revises: e192906f1eae
Create Date: 2026-09-27 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

from gpustack.migrations.utils import column_exists, table_exists


# revision identifiers, used by Alembic.
revision: str = '6d4092d2e980'
down_revision: Union[str, None] = 'e192906f1eae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Hot table and archive must share a column layout; ``UsageDetailsArchiver``
# refuses to start otherwise.
_TABLES = ('model_usage_details', 'model_usage_details_archive')


def upgrade() -> None:
    for table_name in _TABLES:
        if table_exists(table_name) and not column_exists(table_name, 'user_agent'):
            op.add_column(
                table_name,
                sa.Column(
                    'user_agent', sqlmodel.sql.sqltypes.AutoString(), nullable=True
                ),
            )


def downgrade() -> None:
    for table_name in _TABLES:
        if table_exists(table_name) and column_exists(table_name, 'user_agent'):
            op.drop_column(table_name, 'user_agent')
