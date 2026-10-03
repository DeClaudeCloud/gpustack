"""Image verification blocks incompatible schemas and cleans up failed probes."""

import pytest


def test_schema_comparison_reports_changed_and_missing_tables(verifier):
    expected = {"models": {"columns": ["id", "revision_history_limit"]}}
    actual = {"models": {"columns": ["id"]}, "obsolete": {}}
    with pytest.raises(ValueError, match="models.*obsolete"):
        verifier.compare_schemas(expected, actual)


def test_failing_probe_cleans_up_disposable_resources(verifier, monkeypatch):
    calls = []
    monkeypatch.setattr(verifier, "docker", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(verifier, "wait_for_postgres", lambda container: None)
    monkeypatch.setattr(verifier, "snapshot", lambda *args: {})

    def fail(*args):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(verifier, "probe", fail)
    with pytest.raises(RuntimeError, match="migration failed"):
        verifier.verify_images("candidate", ["previous"], "postgres")
    assert any(args[:3] == ("rm", "--force", "--volumes") for args in calls)
    assert any(args[:2] == ("network", "rm") for args in calls)
