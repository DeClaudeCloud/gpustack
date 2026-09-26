"""The embedded gateway's access log as a source of user agents.

Lines are written the way Envoy writes them with GPUStack's access-log format
(one JSON object per line, ``-`` for a missing header).
"""

import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gpustack.gateway.access_log import (
    USER_AGENT_MAX_LENGTH,
    GatewayAccessLog,
    apply_user_agents,
    normalize_user_agent,
)
from gpustack.schemas.model_usage_details import ModelUsageDetails


def _line(request_id, user_agent="curl/8.5.0", **extra):
    return (
        json.dumps(
            {
                "request_id": request_id,
                "user_agent": user_agent,
                "response_code": "200",
                **extra,
            }
        )
        + "\n"
    )


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def log_path(tmp_path):
    path = tmp_path / "access.log"
    path.write_text(_line("before-start"))
    return path


def _append(path, text):
    with open(path, "a") as f:
        f.write(text)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("openai-python/1.51.0", "openai-python/1.51.0"),
        ("  curl/8.5.0 ", "curl/8.5.0"),
        ("-", None),
        ("", None),
        (None, None),
        ("x" * 300, "x" * USER_AGENT_MAX_LENGTH),
    ],
)
def test_normalize_user_agent(value, expected):
    assert normalize_user_agent(value) == expected


def test_reads_appended_lines_from_the_end_of_the_file(log_path):
    access_log = GatewayAccessLog(str(log_path))
    access_log.poll()
    # Written before the server started: its report will never arrive.
    assert access_log.user_agent_for("before-start") is None

    _append(log_path, _line("r1") + _line("r2", "-") + "not json\n")
    access_log.poll()
    assert access_log.user_agent_for("r1") == "curl/8.5.0"
    assert access_log.user_agent_for("r2") is None


def test_holds_a_partial_line_until_it_is_finished(log_path):
    access_log = GatewayAccessLog(str(log_path))
    access_log.poll()
    line = _line("r1")
    _append(log_path, line[:10])
    access_log.poll()
    assert access_log.user_agent_for("r1") is None
    _append(log_path, line[10:])
    access_log.poll()
    assert access_log.user_agent_for("r1") == "curl/8.5.0"


def test_follows_a_rotation(log_path):
    access_log = GatewayAccessLog(str(log_path))
    access_log.poll()
    _append(log_path, _line("old-1"))
    # logrotate's ``create`` mode: rename, new empty file, Envoy reopens.
    os.rename(log_path, str(log_path) + ".1")
    _append(str(log_path) + ".1", _line("old-2"))
    log_path.write_text(_line("new-1"))
    access_log.poll()
    assert access_log.user_agent_for("old-1") == "curl/8.5.0"
    assert access_log.user_agent_for("old-2") == "curl/8.5.0"
    assert access_log.user_agent_for("new-1") == "curl/8.5.0"


def test_follows_an_in_place_truncation(log_path):
    access_log = GatewayAccessLog(str(log_path))
    access_log.poll()
    _append(log_path, _line("r1") + _line("r2") + _line("r3"))
    access_log.poll()
    # Shorter than what was already read, so the reader has to start over.
    log_path.write_text(_line("after-truncate"))
    access_log.poll()
    assert access_log.user_agent_for("after-truncate") == "curl/8.5.0"


def test_waits_for_a_file_that_does_not_exist_yet(tmp_path):
    path = tmp_path / "access.log"
    access_log = GatewayAccessLog(str(path))
    access_log.poll()
    path.write_text("")
    access_log.poll()
    _append(path, _line("r1"))
    access_log.poll()
    assert access_log.user_agent_for("r1") == "curl/8.5.0"


def test_deferred_rows_resolve_when_their_line_arrives(log_path):
    clock = FakeClock()
    access_log = GatewayAccessLog(str(log_path), retention_seconds=60, clock=clock)
    access_log.poll()
    access_log.defer(["early", "never"])
    assert access_log.take_resolved() == {}

    _append(log_path, _line("early", "httpx/0.27"))
    access_log.poll()
    assert access_log.take_resolved() == {"early": "httpx/0.27"}
    # Resolved once, then forgotten.
    assert access_log.take_resolved() == {}

    # A line that never appears is given up on after the retention window.
    clock.now += 61
    assert access_log.take_resolved() == {}
    _append(log_path, _line("never"))
    access_log.poll()
    assert access_log.take_resolved() == {}


def test_user_agents_expire_after_the_retention_window(log_path):
    clock = FakeClock()
    access_log = GatewayAccessLog(str(log_path), retention_seconds=60, clock=clock)
    access_log.poll()
    _append(log_path, _line("r1"))
    access_log.poll()
    clock.now += 61
    access_log.expire()
    assert access_log.user_agent_for("r1") is None


def test_keeps_at_most_max_entries(log_path):
    access_log = GatewayAccessLog(str(log_path), max_entries=2)
    access_log.poll()
    _append(log_path, _line("r1") + _line("r2") + _line("r3"))
    access_log.poll()
    assert access_log.user_agent_for("r1") is None
    assert access_log.user_agent_for("r3") == "curl/8.5.0"


@pytest_asyncio.fixture
async def session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(ModelUsageDetails.__table__.create)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        now = datetime(2026, 9, 27, 10, 0, 0)
        for id_, request_id, user_agent in [
            (1, "a", None),
            (2, "b", None),
            (3, "c", "kept/1.0"),
        ]:
            s.add(
                ModelUsageDetails(
                    id=id_,
                    model_name="qwen3",
                    date=date(2026, 9, 27),
                    prompt_token_count=1,
                    completion_token_count=1,
                    request_id=request_id,
                    user_agent=user_agent,
                    created_at=now,
                    updated_at=now,
                )
            )
        await s.commit()

        @asynccontextmanager
        async def _session():
            yield s

        monkeypatch.setattr("gpustack.server.db.async_session", _session)
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_user_agents_fills_only_missing_values(session):
    await apply_user_agents({"a": "curl/8.5.0", "b": "httpx/0.27", "c": "other/2"})

    rows = {
        r.request_id: r.user_agent
        for r in await session.exec(select(ModelUsageDetails))
    }
    assert rows == {"a": "curl/8.5.0", "b": "httpx/0.27", "c": "kept/1.0"}
