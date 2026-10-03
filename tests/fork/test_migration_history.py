"""Published revisions stay immutable as upstream and fork histories diverge."""

import shutil

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _revision(directory, revision, parent, statement="", prefix=""):
    path = directory / f"{prefix}{revision}.py"
    path.write_text(
        "from alembic import op\n"
        f"revision = {revision!r}\ndown_revision = {parent!r}\n"
        "branch_labels = None\ndepends_on = None\n"
        f"def upgrade():\n    op.execute({statement!r})\n"
        "def downgrade():\n    pass\n"
    )
    return path


@pytest.mark.parametrize("starting_branch", ["fork_tip", "upstream_tip"])
def test_merge_runs_missing_branch_without_rewriting_history(
    history, tmp_path, starting_branch
):
    versions = tmp_path / "versions"
    versions.mkdir()
    _revision(versions, "root", None, "CREATE TABLE canary (id INTEGER PRIMARY KEY)")
    _revision(
        versions, "fork_tip", "root", "ALTER TABLE canary ADD COLUMN fork_value INTEGER"
    )
    _revision(
        versions,
        "upstream_tip",
        "root",
        "ALTER TABLE canary ADD COLUMN upstream_value INTEGER",
    )
    original = {p.name: p.read_bytes() for p in versions.glob("*.py")}
    merged = history.merge_heads(versions)
    assert merged is not None
    assert all(
        (versions / name).read_bytes() == value for name, value in original.items()
    )
    assert history.merge_heads(versions) is None

    (tmp_path / "env.py").write_text(
        "from alembic import context\nfrom sqlalchemy import create_engine\n"
        "with create_engine(context.config.get_main_option('sqlalchemy.url')).connect() as conn:\n"
        "    context.configure(connection=conn)\n"
        "    with context.begin_transaction():\n        context.run_migrations()\n"
    )
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config()
    config.set_main_option("script_location", str(tmp_path))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, starting_branch)
    command.upgrade(config, "head")
    engine = sa.create_engine(url)
    assert {c["name"] for c in sa.inspect(engine).get_columns("canary")} == {
        "id",
        "fork_value",
        "upstream_value",
    }
    engine.dispose()


@pytest.fixture
def revisions(history, tmp_path):
    previous = tmp_path / "previous"
    previous.mkdir()
    _revision(previous, "root", None, "SELECT 1")
    _revision(previous, "fork_tip", "root", "SELECT 2")
    current = tmp_path / "current"
    shutil.copytree(previous, current)
    return previous, current


def test_comments_and_file_renames_do_not_require_repair(history, revisions):
    previous, current = revisions
    path = current / "root.py"
    path.write_text('"""Documentation only."""\n# A comment\n' + path.read_text())
    path.rename(current / "renamed_root.py")
    history.check_history(
        history.read_revisions(previous), history.read_revisions(current), {}
    )


@pytest.mark.parametrize("change", ["body", "ancestry", "removed"])
def test_published_history_changes_stop_sync(history, revisions, change):
    previous, current = revisions
    if change == "body":
        _revision(current, "root", None, "SELECT 3")
    elif change == "ancestry":
        _revision(current, "fork_tip", None, "SELECT 2")
    else:
        (current / "fork_tip.py").unlink()
    with pytest.raises(ValueError, match="Published migration"):
        history.check_history(
            history.read_revisions(previous), history.read_revisions(current), {}
        )


@pytest.mark.parametrize(
    "invalid", [None, "fingerprint", "published", "ancestry", "reason"]
)
def test_mutated_bundle_requires_exact_pending_repair(history, revisions, invalid):
    previous, current = revisions
    _revision(current, "root", None, "SELECT 3")
    _revision(
        current, "repair", "fork_tip" if invalid != "ancestry" else "root", "SELECT 4"
    )
    old, new = history.read_revisions(previous), history.read_revisions(current)
    repairs = {
        "root": {
            "fingerprint": new["root"]["fingerprint"],
            "repair_revision": "repair",
            "reason": "Backfill the bundle's schema change",
        }
    }
    if invalid == "fingerprint":
        repairs["root"]["fingerprint"] = "incorrect"
    elif invalid == "published":
        repairs["root"]["repair_revision"] = "fork_tip"
    elif invalid == "reason":
        repairs["root"].pop("reason")
    if invalid is None:
        history.check_history(old, new, repairs)
    else:
        with pytest.raises(ValueError):
            history.check_history(old, new, repairs)


@pytest.mark.parametrize("malformed", ["cycle", "missing", "duplicate", "unparsed"])
def test_malformed_history_is_rejected(history, tmp_path, malformed):
    _revision(tmp_path, "root", "root" if malformed == "cycle" else None)
    if malformed == "missing":
        _revision(tmp_path, "tip", "absent")
    elif malformed == "duplicate":
        _revision(tmp_path, "root", None, prefix="duplicate_")
    elif malformed == "unparsed":
        (tmp_path / "other.py").write_text("revision = 'other'\n")
    with pytest.raises(ValueError):
        history.read_revisions(tmp_path)
