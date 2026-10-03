"""Run migration and schema checks inside the image being verified."""

import argparse
import json
import os
from pathlib import Path


def migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config
    import gpustack

    config = Config()
    config.set_main_option(
        "script_location", str(Path(gpustack.__file__).parent / "migrations")
    )
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.set_main_option("called_by_db_migration", "true")
    command.upgrade(config, "head")


def seed(url: str) -> None:
    import sqlalchemy as sa
    from gpustack.schemas.clusters import Cluster
    from gpustack.schemas.models import Model, SourceEnum
    from sqlmodel import Session

    engine = sa.create_engine(url)
    with Session(engine) as session:
        owner_id = session.execute(
            sa.text("SELECT id FROM principals WHERE kind = 'ORG' AND name = 'default'")
        ).scalar_one()
        cluster = Cluster(name="fork-upgrade-cluster", owner_principal_id=owner_id)
        session.add(cluster)
        session.flush()
        model = Model(
            name="fork-upgrade-canary",
            source=SourceEnum.LOCAL_PATH,
            local_path="/ci/canary",
            description="Preserve this deployment on upgrade",
            replicas=0,
            backend_parameters=["--ci-canary"],
            distributed_inference_across_workers=False,
            cluster_id=cluster.id,
            owner_principal_id=owner_id,
        )
        session.add(model)
        session.commit()
    engine.dispose()


def verify(url: str) -> None:
    import sqlalchemy as sa
    from gpustack import schemas  # noqa: F401  register every application table
    from gpustack.schemas.model_routes import MyModel
    from sqlmodel import SQLModel

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        for table in SQLModel.metadata.sorted_tables:
            # This view is created by runtime metadata events, not migrations.
            if table.name == MyModel.__tablename__:
                continue
            connection.execute(sa.select(table).limit(0))
        row = connection.execute(
            sa.text(
                "SELECT description, replicas, backend_parameters FROM models "
                "WHERE name = 'fork-upgrade-canary'"
            )
        ).one()
        assert tuple(row) == (
            "Preserve this deployment on upgrade",
            0,
            ["--ci-canary"],
        ), f"Upgrade changed the canary deployment: {row!r}"
    engine.dispose()


def schema(url: str) -> dict:
    import sqlalchemy as sa

    engine = sa.create_engine(url)
    inspector = sa.inspect(engine)
    result = {}
    for table in sorted(inspector.get_table_names()):
        result[table] = {
            "columns": {
                c["name"]: {
                    "type": str(c["type"]),
                    "nullable": c["nullable"],
                    "default": c.get("default"),
                }
                for c in inspector.get_columns(table)
            },
            "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
            "unique": sorted(
                sorted(c["column_names"])
                for c in inspector.get_unique_constraints(table)
            ),
            "foreign_keys": sorted(
                (
                    f["constrained_columns"],
                    f["referred_table"],
                    f["referred_columns"],
                    f["options"],
                )
                for f in inspector.get_foreign_keys(table)
            ),
            "indexes": sorted(
                (i["name"], i["column_names"], i["unique"])
                for i in inspector.get_indexes(table)
            ),
        }
    engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("snapshot", "migrate", "seed", "verify", "schema")
    )
    args = parser.parse_args()
    if args.action == "snapshot":
        import gpustack

        versions = Path(gpustack.__file__).parent / "migrations/versions"
        print(json.dumps({p.name: p.read_text() for p in versions.glob("*.py")}))
        return
    url = os.environ["GPUSTACK_UPGRADE_DATABASE_URL"]
    if args.action == "schema":
        print(json.dumps(schema(url), sort_keys=True))
    else:
        {"migrate": migrate, "seed": seed, "verify": verify}[args.action](url)


if __name__ == "__main__":
    main()
