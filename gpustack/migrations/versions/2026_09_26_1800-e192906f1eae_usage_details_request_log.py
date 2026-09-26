"""usage details: request log columns

Two per-request columns on ``model_usage_details`` and its archive, plus
one index, for the request-log view under Usage:

1. ``status_code`` — the HTTP status the caller received. Reported by the
   direct (non-gateway) inference path today, and accepted from any
   gateway report that carries it. NULL means "not reported", never
   "succeeded": the read side derives an outcome from the other columns
   when it is absent.

2. ``stream`` — whether the caller asked for a streamed response. Same
   reporting rule as ``status_code``. When NULL the read side falls back
   to ``ttft_ms``, which the gateway only reports for streams.

3. ``ix_model_usage_details_created_at`` — the request log filters and
   buckets by ``created_at`` (the request's completion wall-clock, see
   ``_resolve_metric_datetime``) and tails newest-first. Without an index
   every page load and every live-tail poll is a full scan of up to
   ``GPUSTACK_USAGE_DETAILS_RETENTION_MONTHS`` of rows. Hot table only:
   the archive is not read by the request log.

Both columns are nullable with no backfill. Rows written before this
revision have no such values, and a default could only be a fabricated
one.

Revision ID: e192906f1eae
Revises: f4a5b6c7d8e9
Create Date: 2026-09-26 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from gpustack.migrations.utils import column_exists, index_exists, table_exists


# revision identifiers, used by Alembic.
revision: str = 'e192906f1eae'
down_revision: Union[str, None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Hot table and archive must share a column layout; ``UsageDetailsArchiver``
# refuses to start otherwise. See the f1a2b3c4d5e6 revision for why.
_TABLES = ('model_usage_details', 'model_usage_details_archive')

_COLUMNS = (
    ('status_code', sa.Integer()),
    ('stream', sa.Boolean()),
)

_HOT_TABLE = 'model_usage_details'
_CREATED_AT_INDEX = 'ix_model_usage_details_created_at'


def upgrade() -> None:
    for table_name in _TABLES:
        if not table_exists(table_name):
            continue
        for column_name, column_type in _COLUMNS:
            if column_exists(table_name, column_name):
                continue
            op.add_column(
                table_name, sa.Column(column_name, column_type, nullable=True)
            )

    if table_exists(_HOT_TABLE) and not index_exists(_HOT_TABLE, _CREATED_AT_INDEX):
        op.create_index(_CREATED_AT_INDEX, _HOT_TABLE, ['created_at'])


def downgrade() -> None:
    if table_exists(_HOT_TABLE) and index_exists(_HOT_TABLE, _CREATED_AT_INDEX):
        op.drop_index(_CREATED_AT_INDEX, table_name=_HOT_TABLE)

    for table_name in _TABLES:
        if not table_exists(table_name):
            continue
        for column_name, _ in _COLUMNS:
            if column_exists(table_name, column_name):
                op.drop_column(table_name, column_name)
