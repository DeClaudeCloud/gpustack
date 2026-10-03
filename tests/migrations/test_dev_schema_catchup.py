"""Existing dev databases receive schema objects without resetting revisions."""

from itertools import product
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


@pytest.fixture
def database(tmp_path):
    url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE models (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO models VALUES (1)"))
        connection.execute(sa.text("CREATE TABLE principals (id INTEGER PRIMARY KEY)"))
        connection.execute(
            sa.text("CREATE TABLE cache_service_instances (id INTEGER PRIMARY KEY)")
        )
        connection.execute(sa.text("INSERT INTO cache_service_instances VALUES (1)"))
        connection.execute(
            sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            sa.text("INSERT INTO alembic_version VALUES ('6d4092d2e980')")
        )
    config = Config()
    config.set_main_option("sqlalchemy.url", url)
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "gpustack/migrations"),
    )
    yield engine, config
    engine.dispose()


@pytest.mark.parametrize(
    "has_limit,has_history,has_cache_claim",
    list(product([False, True], repeat=3)),
)
def test_upgrade_from_published_head_preserves_data(
    database, has_limit, has_history, has_cache_claim
):
    engine, config = database
    with engine.begin() as connection:
        if has_limit:
            connection.execute(
                sa.text(
                    "ALTER TABLE models ADD COLUMN revision_history_limit INTEGER NOT NULL DEFAULT 10"
                )
            )
            connection.execute(sa.text("UPDATE models SET revision_history_limit = 3"))
        if has_history:
            connection.execute(
                sa.text(
                    "CREATE TABLE model_revisions (id INTEGER PRIMARY KEY, model_id INTEGER, "
                    "revision INTEGER, spec JSON, created_at DATETIME, created_by INTEGER)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO model_revisions VALUES (1, 1, 1, '{}', '2026-10-01', NULL)"
                )
            )
        if has_cache_claim:
            connection.execute(
                sa.text(
                    "ALTER TABLE cache_service_instances ADD COLUMN computed_resource_claim JSON"
                )
            )
            connection.execute(
                sa.text(
                    "UPDATE cache_service_instances SET computed_resource_claim = '{\"ram\": 123}'"
                )
            )

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT id, revision_history_limit FROM models")
        ).all() == [(1, 3 if has_limit else 10)]
        claim = connection.execute(
            sa.text(
                "SELECT computed_resource_claim FROM cache_service_instances WHERE id = 1"
            )
        ).scalar()
        assert claim == ('{"ram": 123}' if has_cache_claim else None)
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM model_revisions")
        ).scalar() == int(has_history)
        assert (
            connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar()
            == ScriptDirectory.from_config(config).get_current_head()
        )

    command.downgrade(config, "6d4092d2e980")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM model_revisions")
        ).scalar() == int(has_history)
