"""Load fork-only automation without adding it to the GPUStack distribution."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def history(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / ".github/fork")
    )
    return importlib.import_module("migration_history")


@pytest.fixture
def verifier(history):
    return importlib.import_module("verify_image_upgrade")
