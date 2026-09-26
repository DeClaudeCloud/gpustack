"""Request-log read API over ``model_usage_details``.

Runs the real SQL (outcome ``CASE``, duration arithmetic, bucketing, scoping)
against an in-memory SQLite engine, because what matters is what the queries
return for rows shaped the way the ingest path writes them.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from gpustack.api.exceptions import ForbiddenException, InvalidException
from gpustack.routes.request_logs import (
    RequestLogFilters,
    bucket_seconds_for,
    list_request_logs,
    parse_sort,
    request_log_filters,
    request_log_stats,
)
from gpustack.schemas.model_routes import ModelRoute
from gpustack.schemas.model_usage import OperationEnum
from gpustack.schemas.model_usage_details import ModelUsageDetails
from gpustack.schemas.principals import Principal

T0 = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
WINDOW = dict(start=T0 - timedelta(hours=1), end=T0 + timedelta(minutes=10))

ADMIN = SimpleNamespace(id=1, is_admin=True)
ALICE = SimpleNamespace(id=7, is_admin=False)
NO_ORG = SimpleNamespace(
    current_principal_id=None, org_role=None, current_is_personal_scope=False
)


def _row(id_, *, at, duration_ms=1000, **kw):
    """A details row as the ingest path writes one: ``created_at`` is the
    completion wall-clock, ``started_at`` is ``duration_ms`` before it."""
    base = dict(
        id=id_,
        user_id=1,
        user_name="admin",
        model_name="qwen3",
        model_route_id=10,
        model_route_name="qwen3",
        api_key_id=5,
        api_key_name="ci",
        date=at.date(),
        prompt_token_count=10,
        completion_token_count=90,
        completed=True,
        upstream_response_id=f"chatcmpl-{id_}",
        request_id=f"req-{id_}",
        started_at=at - timedelta(milliseconds=duration_ms),
        completed_at=at,
        created_at=at,
        updated_at=at,
    )
    base.update(kw)
    return ModelUsageDetails(**base)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for table in (Principal, ModelRoute, ModelUsageDetails):
            await conn.run_sync(table.__table__.create)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        s.add(
            ModelRoute(
                id=10,
                name="qwen3",
                categories=["llm"],
                owner_principal_id=1,
                created_at=T0,
                updated_at=T0,
            )
        )
        s.add_all(
            [
                # Streamed success: the gateway reports ttft but no stream flag.
                _row(
                    1,
                    at=T0 - timedelta(minutes=50),
                    duration_ms=2000,
                    ttft_ms=500,
                    user_agent="openai-python/1.51.0",
                ),
                # Non-streamed success.
                _row(2, at=T0 - timedelta(minutes=40), duration_ms=4000),
                # Upstream error as the gateway writes it: completed, no
                # tokens, no model response id, no status.
                _row(
                    3,
                    at=T0 - timedelta(minutes=30),
                    duration_ms=20,
                    prompt_token_count=0,
                    completion_token_count=0,
                    upstream_response_id=None,
                ),
                # Client went away mid-stream: usage never observed.
                _row(
                    4,
                    at=T0 - timedelta(minutes=20),
                    completed=False,
                    completion_token_count=0,
                    upstream_response_id=None,
                ),
                # Direct path: explicit status and stream flag, owned by alice.
                _row(
                    5,
                    at=T0 - timedelta(minutes=10),
                    user_id=7,
                    user_name="alice",
                    api_key_id=None,
                    api_key_name=None,
                    request_id=None,
                    status_code=200,
                    stream=False,
                    operation=OperationEnum.EMBEDDING,
                    prompt_token_count=0,
                    completion_token_count=0,
                    upstream_response_id=None,
                ),
                # A reported error status wins over tokens that look healthy.
                _row(6, at=T0 - timedelta(minutes=5), status_code=503),
                # Fallback: two attempts of one downstream request, the first
                # failed, the retry succeeded.
                _row(
                    7,
                    at=T0 - timedelta(minutes=2),
                    request_id="req-fallback",
                    status_code=500,
                ),
                _row(8, at=T0 - timedelta(minutes=1), request_id="req-fallback"),
                # Outside the window.
                _row(9, at=T0 - timedelta(hours=3)),
            ]
        )
        await s.commit()
        yield s


def _filters(**kw):
    return RequestLogFilters(**{**WINDOW, **kw})


@pytest.mark.asyncio
async def test_rows_carry_derived_outcome_type_and_stream(session):
    result = await list_request_logs(session, ADMIN, NO_ORG, _filters(), per_page=50)
    by_id = {item.id: item for item in result.items}

    assert result.pagination.total == 8
    assert [item.id for item in result.items] == [8, 7, 6, 5, 4, 3, 2, 1]

    assert by_id[1].status == "success"
    assert by_id[1].stream is True
    assert by_id[1].type == "llm"
    assert by_id[1].duration_ms == 2000
    # 90 tokens over the 1.5 s after the first token.
    assert by_id[1].tokens_per_second == 60.0
    assert by_id[1].user_agent == "openai-python/1.51.0"
    assert by_id[2].user_agent is None

    assert by_id[2].stream is False
    assert by_id[2].tokens_per_second == 22.5

    assert by_id[3].status == "error"
    assert by_id[4].status == "incomplete"
    # A known 200 is a success even without tokens or a response id.
    assert by_id[5].status == "success"
    assert by_id[5].type == "embedding"
    assert by_id[6].status == "error"
    assert by_id[6].status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters, expected",
    [
        (dict(statuses=["error"]), [7, 6, 3]),
        (dict(statuses=["incomplete", "error"]), [7, 6, 4, 3]),
        (dict(stream=True), [1]),
        (dict(stream=False), [8, 7, 6, 5, 4, 3, 2]),
        (dict(search="req-fallback"), [8, 7]),
        (dict(search="chatcmpl-2"), [2]),
        (dict(search="wen"), [8, 7, 6, 5, 4, 3, 2, 1]),
        (dict(api_key_ids=[5], user_ids=[7]), []),
        (dict(user_ids=[7]), [5]),
        (dict(route_ids=[99]), []),
    ],
)
async def test_filters(session, filters, expected):
    result = await list_request_logs(
        session, ADMIN, NO_ORG, _filters(**filters), per_page=50
    )
    assert [item.id for item in result.items] == expected


@pytest.mark.asyncio
async def test_member_sees_only_own_requests(session):
    result = await list_request_logs(session, ALICE, NO_ORG, _filters(), per_page=50)
    assert [item.id for item in result.items] == [5]

    with pytest.raises(ForbiddenException):
        await list_request_logs(session, ALICE, NO_ORG, _filters(user_ids=[1]))


@pytest.mark.asyncio
async def test_sorting_and_pagination(session):
    async def page(n):
        return await list_request_logs(
            session,
            ADMIN,
            NO_ORG,
            _filters(),
            page=n,
            per_page=2,
            order_by=parse_sort("-duration_ms"),
        )

    first, last = await page(1), await page(4)
    assert first.pagination.total == 8
    assert first.pagination.totalPage == 4
    # Longest first (4000 ms, 2000 ms); the 20 ms error is last. The five
    # 1000 ms rows in between tie, and SQLite's julianday arithmetic does not
    # compute exact ties, so their relative order is not asserted here.
    assert [item.id for item in first.items] == [2, 1]
    assert last.items[-1].id == 3


def test_parse_sort_rejects_unknown_fields():
    assert parse_sort("-ttft_ms,created_at") == [
        ("ttft_ms", "desc"),
        ("created_at", "asc"),
    ]
    with pytest.raises(InvalidException):
        parse_sort("user_name")


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(status=["exploded"]),
        dict(scope="everyone"),
        dict(start=T0, end=T0),
    ],
)
def test_filter_validation(kwargs):
    params = dict(
        scope="all",
        start=None,
        end=None,
        status=None,
        stream=None,
        route_id=None,
        api_key_id=None,
        user_id=None,
        search=None,
    )
    params.update(kwargs)
    with pytest.raises(InvalidException):
        request_log_filters(**params)


def test_filter_times_are_normalized_to_utc():
    naive = datetime(2026, 9, 26, 12, 0, 0)
    filters = request_log_filters(
        scope="all",
        start=naive,
        end=None,
        status=["error", "error"],
        stream=None,
        route_id=None,
        api_key_id=None,
        user_id=None,
        search="  req-1 ",
    )
    assert filters.start == naive.replace(tzinfo=timezone.utc)
    assert filters.statuses == ["error"]
    assert filters.search == "req-1"


@pytest.mark.asyncio
async def test_stats_totals(session):
    stats = await request_log_stats(session, ADMIN, NO_ORG, _filters())

    assert stats.total_requests == 8
    assert stats.success_requests == 4
    assert stats.error_requests == 3
    assert stats.incomplete_requests == 1
    assert stats.success_rate == 50.0
    # Seven downstream requests: req-fallback counts once, as a success.
    assert stats.user_success_rate == pytest.approx(round(4 * 100 / 7, 2))
    assert stats.avg_ttft_ms == 500.0
    assert stats.p95_latency_ms == 4000.0
    assert stats.prompt_tokens == 60
    assert stats.completion_tokens == 450
    assert stats.total_tokens == 510

    # The hour before the window holds the one out-of-range row.
    assert stats.previous is not None
    assert stats.previous.total_requests == 0


@pytest.mark.asyncio
async def test_stats_buckets_are_dense_and_counted(session):
    stats = await request_log_stats(session, ADMIN, NO_ORG, _filters())

    # A 70 minute window: 60 s buckets would be 70 bars, so 5 minutes.
    assert stats.bucket_seconds == 300
    assert sum(b.success + b.error + b.incomplete for b in stats.buckets) == 8
    assert stats.buckets[0].time <= WINDOW["start"]
    assert stats.buckets[-1].time < WINDOW["end"]
    steps = {
        (b.time - a.time).total_seconds()
        for a, b in zip(stats.buckets, stats.buckets[1:])
    }
    assert steps == {300}
    errors = [b for b in stats.buckets if b.error]
    assert sum(b.error for b in errors) == 3
    assert sum(b.tokens for b in stats.buckets) == 510
    # Row 2 (4000 ms) sits alone in its bucket.
    (row_2_bucket,) = [b for b in stats.buckets if b.avg_latency_ms == 4000.0]
    assert row_2_bucket.success == 1
    assert all(
        b.avg_latency_ms is None
        for b in stats.buckets
        if not b.success + b.error + b.incomplete
    )


@pytest.mark.asyncio
async def test_stats_scope_and_empty_window(session):
    stats = await request_log_stats(session, ALICE, NO_ORG, _filters())
    assert stats.total_requests == 1

    empty = await request_log_stats(
        session,
        ADMIN,
        NO_ORG,
        RequestLogFilters(start=T0 + timedelta(days=1), end=T0 + timedelta(days=2)),
    )
    assert empty.total_requests == 0
    assert empty.success_rate is None
    assert empty.p95_latency_ms is None
    assert all(not b.success for b in empty.buckets)


@pytest.mark.parametrize(
    "span, expected",
    [
        (timedelta(minutes=5), 10),
        (timedelta(hours=1), 60),
        (timedelta(hours=24), 1800),
        (timedelta(days=7), 10800),
        (timedelta(days=90), 86400),
    ],
)
def test_bucket_size_targets_about_sixty_bars(span, expected):
    assert bucket_seconds_for(T0, T0 + span) == expected
