"""Per-request log under Usage: ``/v2/usage/request-logs``.

A read view over ``model_usage_details``, which the usage ingest path already
writes once per inference request. Two endpoints share one filter set:

* ``GET /usage/request-logs`` — the paginated rows, newest first by default.
* ``GET /usage/request-logs/stats`` — headline totals, a request-volume
  histogram, and the same totals for the preceding window (for trends).

Visibility follows the Usage page: platform admins and real Org owners get
the ``all`` view of their tenant, everyone else only their own requests.

Time: rows are filtered, bucketed and tailed on ``created_at``, which the
ingest path pins to the request's completion wall-clock. Completion order is
the order rows become visible, so a live tail only ever grows at the top.

Outcome: ``status_code`` is authoritative when the reporter carried it (the
direct inference path does; the gateway token-usage plugin does not yet).
Otherwise it is derived from what every report has — see
``outcome_expression``.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Annotated, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from sqlalchemy import (
    Float,
    Integer,
    String,
    and_,
    case,
    cast,
    extract,
    literal,
    literal_column,
)
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from gpustack.api.exceptions import ForbiddenException, InvalidException
from gpustack.api.tenant import TenantContext
from gpustack.routes.usage import _resolve_effective_scope
from gpustack.schemas.common import Pagination
from gpustack.schemas.model_routes import ModelRoute
from gpustack.schemas.model_usage_details import ModelUsageDetails as D
from gpustack.schemas.request_logs import (
    REQUEST_LOG_SORT_FIELDS,
    REQUEST_LOG_STATUS_ERROR,
    REQUEST_LOG_STATUS_INCOMPLETE,
    REQUEST_LOG_STATUS_SUCCESS,
    REQUEST_LOG_STATUSES,
    RequestLogBucket,
    RequestLogItem,
    RequestLogList,
    RequestLogStats,
    RequestLogTotals,
)
from gpustack.schemas.usage import USAGE_SCOPE_ALL, USAGE_SCOPE_SELF
from gpustack.schemas.users import User
from gpustack.server.deps import CurrentUserDep, SessionDep, TenantContextDep

router = APIRouter()

MAX_PER_PAGE = 500
# Candidate histogram widths, smallest first. The widest range the UI offers
# is 30 days; 1 day buckets keep that at 30 bars.
_BUCKET_SIZES = (10, 30, 60, 300, 600, 900, 1800, 3600, 10800, 21600, 43200, 86400)
_TARGET_BUCKETS = 60
# Status 499 is the de-facto "client closed request"; it is an incomplete
# request, not a failed one.
_CLIENT_CLOSED_STATUS = 499


@dataclass
class RequestLogFilters:
    scope: str = USAGE_SCOPE_ALL
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    statuses: List[str] = field(default_factory=list)
    stream: Optional[bool] = None
    route_ids: List[int] = field(default_factory=list)
    api_key_ids: List[int] = field(default_factory=list)
    user_ids: List[int] = field(default_factory=list)
    search: Optional[str] = None


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def request_log_filters(
    scope: Annotated[str, Query()] = USAGE_SCOPE_ALL,
    start: Annotated[
        Optional[datetime],
        Query(description="Inclusive lower bound on completion time (ISO 8601)."),
    ] = None,
    end: Annotated[
        Optional[datetime],
        Query(description="Exclusive upper bound on completion time (ISO 8601)."),
    ] = None,
    status: Annotated[Optional[List[str]], Query()] = None,
    stream: Annotated[Optional[bool], Query()] = None,
    route_id: Annotated[Optional[List[int]], Query()] = None,
    api_key_id: Annotated[Optional[List[int]], Query()] = None,
    user_id: Annotated[Optional[List[int]], Query()] = None,
    search: Annotated[
        Optional[str],
        Query(description="Matches request id, upstream response id or model name."),
    ] = None,
) -> RequestLogFilters:
    """FastAPI dependency parsing the filters both request-log endpoints share.

    Naive ``start`` / ``end`` values are read as UTC; repeated ``status``
    values are collapsed.

    Raises:
        InvalidException: an unknown scope or status, or ``start`` not before
            ``end``.
    """
    if scope not in (USAGE_SCOPE_SELF, USAGE_SCOPE_ALL):
        raise InvalidException(message=f"Unsupported scope: {scope}")
    statuses = list(dict.fromkeys(status or []))
    unknown = [s for s in statuses if s not in REQUEST_LOG_STATUSES]
    if unknown:
        raise InvalidException(
            message=f"Unsupported status: {', '.join(unknown)}. "
            f"Allowed: {', '.join(REQUEST_LOG_STATUSES)}"
        )
    start, end = _as_utc(start), _as_utc(end)
    if start and end and start >= end:
        raise InvalidException(message="start must be before end")
    return RequestLogFilters(
        scope=scope,
        start=start,
        end=end,
        statuses=statuses,
        stream=stream,
        route_ids=route_id or [],
        api_key_ids=api_key_id or [],
        user_ids=user_id or [],
        search=(search or "").strip() or None,
    )


RequestLogFiltersDep = Annotated[RequestLogFilters, Depends(request_log_filters)]


# --------------------------------------------------------------------------
# Derived columns
# --------------------------------------------------------------------------


def _token_total() -> ColumnElement[int]:
    return D.prompt_token_count + D.completion_token_count


def outcome_expression() -> ColumnElement[str]:
    """SQL ``CASE`` yielding one of ``REQUEST_LOG_STATUSES`` per row.

    With a reported ``status_code`` the answer is direct. Without one:

    * ``completed`` false — the response ended before the usage report was
      observed: incomplete.
    * no tokens either way *and* no upstream response id — the upstream never
      produced a model response: error. The gateway writes exactly this for
      upstream 4xx/5xx replies (verified against 0.4.0 of the plugin).
    * anything else: success.
    """
    return case(
        (
            D.status_code == _CLIENT_CLOSED_STATUS,
            literal(REQUEST_LOG_STATUS_INCOMPLETE),
        ),
        (D.status_code >= 400, literal(REQUEST_LOG_STATUS_ERROR)),
        (D.completed.is_(False), literal(REQUEST_LOG_STATUS_INCOMPLETE)),
        (
            and_(
                D.status_code.is_(None),
                _token_total() == 0,
                D.upstream_response_id.is_(None),
            ),
            literal(REQUEST_LOG_STATUS_ERROR),
        ),
        else_=literal(REQUEST_LOG_STATUS_SUCCESS),
    )


def _stream_condition() -> ColumnElement[bool]:
    """Reported ``stream`` flag, else "the gateway saw a first chunk".

    The gateway reports ``ttft_ms`` for streamed responses only, so it stands
    in for the flag on rows whose reporter didn't carry one.
    """
    return or_(D.stream.is_(True), and_(D.stream.is_(None), D.ttft_ms.is_not(None)))


def _dialect(session: AsyncSession) -> str:
    return session.get_bind().dialect.name


def duration_ms_expression(session: AsyncSession) -> ColumnElement:
    """``completed_at - started_at`` in milliseconds; NULL if either is."""
    dialect = _dialect(session)
    if dialect == "postgresql":
        return extract("epoch", D.completed_at - D.started_at) * 1000
    if dialect == "mysql":
        return (
            func.timestampdiff(
                literal_column("MICROSECOND"), D.started_at, D.completed_at
            )
            / 1000
        )
    # sqlite (test engine)
    return (func.julianday(D.completed_at) - func.julianday(D.started_at)) * 86400000


def _epoch_seconds_expression(session: AsyncSession, column) -> ColumnElement:
    dialect = _dialect(session)
    if dialect == "postgresql":
        # A ``timestamp without time zone`` is read as UTC by EXTRACT(EPOCH),
        # which is what UTCDateTime stores.
        return cast(extract("epoch", column), Integer)
    if dialect == "mysql":
        # Not UNIX_TIMESTAMP(): that reads a DATETIME in the session zone.
        return cast(
            func.timestampdiff(
                literal_column("SECOND"), literal("1970-01-01 00:00:00"), column
            ),
            Integer,
        )
    return cast(func.strftime("%s", column), Integer)


def _tokens_per_second_expression(duration_ms: ColumnElement) -> ColumnElement:
    """Completion tokens over generation time, NULL when there is none."""
    generation_ms = duration_ms - func.coalesce(D.ttft_ms, 0)
    return case(
        (
            and_(D.completion_token_count > 0, generation_ms > 0),
            cast(D.completion_token_count, Float) * 1000 / generation_ms,
        ),
        else_=None,
    )


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------


def _self_scope_consumer_condition(user_id: int, org_id: int):
    # Mirrors ``gpustack.routes.usage._self_scope_consumer_condition`` on the
    # details table: in personal scope the caller's un-attributed rows
    # (cookie-authed Playground traffic) are theirs too.
    if org_id == user_id:
        return or_(D.consumer_principal_id == org_id, D.consumer_principal_id.is_(None))
    return D.consumer_principal_id == org_id


def _conditions(
    session: AsyncSession,
    user: User,
    ctx: TenantContext,
    filters: RequestLogFilters,
    *,
    window: bool = True,
) -> List[Any]:
    """WHERE conditions for the caller's visible, filtered rows.

    ``window=False`` leaves the time bounds out, for callers that apply their
    own (the previous-period totals).
    """
    scope = _resolve_effective_scope(user, ctx, filters.scope)
    org_id = getattr(ctx, "current_principal_id", None)
    conditions: List[Any] = []
    if scope == USAGE_SCOPE_SELF:
        if filters.user_ids:
            raise ForbiddenException(message="No permission to filter by user")
        conditions.append(D.user_id == user.id)
        if org_id is not None:
            conditions.append(_self_scope_consumer_condition(user.id, org_id))
    elif org_id is not None:
        conditions.append(D.consumer_principal_id == org_id)

    if window:
        if filters.start is not None:
            conditions.append(D.created_at >= filters.start)
        if filters.end is not None:
            conditions.append(D.created_at < filters.end)
    if filters.statuses:
        conditions.append(outcome_expression().in_(filters.statuses))
    if filters.stream is not None:
        stream = _stream_condition()
        conditions.append(stream if filters.stream else ~stream)
    if filters.route_ids:
        conditions.append(D.model_route_id.in_(filters.route_ids))
    if filters.api_key_ids:
        conditions.append(D.api_key_id.in_(filters.api_key_ids))
    if filters.user_ids:
        conditions.append(D.user_id.in_(filters.user_ids))
    if filters.search:
        needle = filters.search
        conditions.append(
            or_(
                D.request_id == needle,
                D.upstream_response_id == needle,
                D.model_name.contains(needle, autoescape=True),
                D.model_route_name.contains(needle, autoescape=True),
            )
        )
    return conditions


# --------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------


def _row_type(operation, categories) -> Optional[str]:
    if operation is not None:
        return getattr(operation, "value", str(operation))
    if categories:
        return categories[0]
    return None


def _optional_int(value) -> Optional[int]:
    return None if value is None else int(round(value))


async def list_request_logs(
    session: AsyncSession,
    user: User,
    ctx: TenantContext,
    filters: RequestLogFilters,
    *,
    page: int = 1,
    per_page: int = 25,
    order_by: Optional[List[Tuple[str, str]]] = None,
) -> RequestLogList:
    """One page of the caller's visible requests.

    Args:
        session: Database session.
        user: The caller; decides the effective scope with ``ctx``.
        ctx: The caller's tenant context.
        filters: Time window, outcome, stream, route / API key / user and search.
        page: 1-based page number.
        per_page: Rows per page, at most ``MAX_PER_PAGE``.
        order_by: ``(field, "asc" | "desc")`` pairs from
            ``REQUEST_LOG_SORT_FIELDS``; newest first when omitted.

    Returns:
        The page, with pagination totals over every matching row.

    Raises:
        InvalidException: ``page`` or ``per_page`` is out of range.
        ForbiddenException: a user filter was given in the ``self`` scope.
    """
    if page < 1:
        raise InvalidException(message="page must be >= 1")
    if not 1 <= per_page <= MAX_PER_PAGE:
        raise InvalidException(message=f"perPage must be between 1 and {MAX_PER_PAGE}")

    conditions = _conditions(session, user, ctx, filters)
    duration = duration_ms_expression(session)

    total = (
        await session.exec(select(func.count()).select_from(D).where(*conditions))
    ).one()

    sort_columns: Dict[str, Any] = {
        "created_at": D.created_at,
        "duration_ms": duration,
        "ttft_ms": D.ttft_ms,
        "total_tokens": _token_total(),
    }
    ordering = []
    for name, direction in order_by or [("created_at", "desc")]:
        column = sort_columns[name]
        ordering.append(column.desc() if direction == "desc" else column.asc())
    # id breaks ties so rows completing in the same millisecond keep a stable
    # order across pages and polls.
    ordering.append(D.id.desc())

    statement = (
        select(
            D,
            outcome_expression().label("outcome"),
            _stream_condition().label("is_stream"),
            duration.label("duration_ms"),
            _tokens_per_second_expression(duration).label("tps"),
            ModelRoute.categories,
        )
        .outerjoin(ModelRoute, ModelRoute.id == D.model_route_id)
        .where(*conditions)
        .order_by(*ordering)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = (await session.exec(statement)).all()

    items = [
        RequestLogItem(
            id=row.id,
            request_id=row.request_id,
            upstream_response_id=row.upstream_response_id,
            status=outcome,
            status_code=row.status_code,
            type=_row_type(row.operation, categories),
            stream=bool(is_stream),
            completed=row.completed,
            model_name=row.model_name,
            model_route_id=row.model_route_id,
            model_route_name=row.model_route_name,
            provider_name=row.provider_name,
            cluster_name=row.cluster_name,
            user_id=row.user_id,
            user_name=row.user_name,
            api_key_id=row.api_key_id,
            api_key_name=row.api_key_name,
            access_key=row.access_key,
            user_agent=row.user_agent,
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_ms=_optional_int(duration_ms),
            ttft_ms=row.ttft_ms,
            prompt_tokens=row.prompt_token_count,
            completion_tokens=row.completion_token_count,
            cached_tokens=row.prompt_cached_token_count,
            total_tokens=row.prompt_token_count + row.completion_token_count,
            tokens_per_second=None if tps is None else round(float(tps), 2),
        )
        for row, outcome, is_stream, duration_ms, tps, categories in rows
    ]
    return RequestLogList(
        items=items,
        pagination=Pagination(
            page=page,
            perPage=per_page,
            total=total,
            totalPage=ceil(total / per_page),
        ),
    )


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------


def bucket_seconds_for(start: datetime, end: datetime) -> int:
    span = (end - start).total_seconds()
    for size in _BUCKET_SIZES:
        if span / size <= _TARGET_BUCKETS:
            return size
    return _BUCKET_SIZES[-1]


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if not denominator:
        return None
    return round(numerator * 100 / denominator, 2)


def _optional_float(value, digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


async def _totals(session: AsyncSession, conditions: List[Any]) -> RequestLogTotals:
    outcome = outcome_expression()
    duration = duration_ms_expression(session)
    stream_ttft = case((_stream_condition(), D.ttft_ms), else_=None)

    aggregate = (
        await session.exec(
            select(
                func.count(),
                func.sum(case((outcome == REQUEST_LOG_STATUS_SUCCESS, 1), else_=0)),
                func.sum(case((outcome == REQUEST_LOG_STATUS_ERROR, 1), else_=0)),
                func.sum(case((outcome == REQUEST_LOG_STATUS_INCOMPLETE, 1), else_=0)),
                func.avg(duration),
                func.avg(stream_ttft),
                func.avg(_tokens_per_second_expression(duration)),
                func.coalesce(func.sum(D.prompt_token_count), 0),
                func.coalesce(func.sum(D.completion_token_count), 0),
            )
            .select_from(D)
            .where(*conditions)
        )
    ).one()
    (
        total,
        success,
        error,
        incomplete,
        avg_latency,
        avg_ttft,
        avg_tps,
        prompt_tokens,
        completion_tokens,
    ) = aggregate
    total = int(total or 0)
    totals = RequestLogTotals(
        total_requests=total,
        success_requests=int(success or 0),
        error_requests=int(error or 0),
        incomplete_requests=int(incomplete or 0),
        avg_latency_ms=_optional_float(avg_latency, 1),
        avg_ttft_ms=_optional_float(avg_ttft, 1),
        avg_tokens_per_second=_optional_float(avg_tps),
        prompt_tokens=int(prompt_tokens),
        completion_tokens=int(completion_tokens),
        total_tokens=int(prompt_tokens) + int(completion_tokens),
    )
    if not total:
        return totals
    totals.success_rate = _rate(totals.success_requests, total)

    # Caller-level success: one vote per downstream request, a success if any
    # of its attempts was. Rows without a request id (the direct path mints
    # none) are their own request.
    request_key = func.coalesce(D.request_id, cast(D.id, String))
    per_request = (
        select(
            func.max(case((outcome == REQUEST_LOG_STATUS_SUCCESS, 1), else_=0)).label(
                "ok"
            )
        )
        .select_from(D)
        .where(*conditions)
        .group_by(request_key)
        .subquery()
    )
    requests, succeeded = (
        await session.exec(
            select(func.count(), func.coalesce(func.sum(per_request.c.ok), 0))
        )
    ).one()
    totals.user_success_rate = _rate(int(succeeded), int(requests))

    # p95 by rank: portable across PostgreSQL / MySQL / SQLite, and one index
    # scan over rows the window already narrowed.
    timed = [*conditions, D.started_at.is_not(None), D.completed_at.is_not(None)]
    timed_count = (
        await session.exec(select(func.count()).select_from(D).where(*timed))
    ).one()
    if timed_count:
        offset = min(int(ceil(timed_count * 0.95)) - 1, timed_count - 1)
        p95 = (
            await session.exec(
                select(duration)
                .select_from(D)
                .where(*timed)
                .order_by(duration.asc())
                .offset(max(offset, 0))
                .limit(1)
            )
        ).first()
        totals.p95_latency_ms = _optional_float(p95, 1)
    return totals


async def request_log_stats(
    session: AsyncSession,
    user: User,
    ctx: TenantContext,
    filters: RequestLogFilters,
) -> RequestLogStats:
    """Headline totals, request-volume buckets and the preceding window's totals.

    Args:
        session: Database session.
        user: The caller; decides the effective scope with ``ctx``.
        ctx: The caller's tenant context.
        filters: As for ``list_request_logs``. An open end is "now" and an
            open start is one hour before the end.

    Returns:
        Totals over the window, one bucket per ``bucket_seconds`` from the
        window's start to its end (empty buckets included), and ``previous``:
        the same totals over the equally long window right before it.

    Raises:
        ForbiddenException: a user filter was given in the ``self`` scope.
    """
    end = filters.end or datetime.now(timezone.utc)
    start = filters.start or end - timedelta(hours=1)
    bucket = bucket_seconds_for(start, end)

    windowed = replace(filters, start=start, end=end)
    conditions = _conditions(session, user, ctx, windowed)
    totals = await _totals(session, conditions)

    outcome = outcome_expression()
    # Integer floor division on a non-negative integer epoch; SQLAlchemy
    # renders ``//`` as ``/`` on PostgreSQL / SQLite and ``DIV`` on MySQL.
    epoch = _epoch_seconds_expression(session, D.created_at)
    bucket_expr = (epoch // bucket) * bucket
    duration = duration_ms_expression(session)
    rows = (
        await session.exec(
            select(
                bucket_expr.label("bucket"),
                outcome.label("outcome"),
                func.count(),
                func.sum(duration),
                func.count(duration),
                func.coalesce(func.sum(_token_total()), 0),
            )
            .select_from(D)
            .where(*conditions)
            .group_by(bucket_expr, outcome)
        )
    ).all()
    per_bucket: Dict[int, Dict[str, Any]] = {}
    for bucket_start, row_outcome, count, duration_sum, timed, tokens in rows:
        entry = per_bucket.setdefault(
            int(bucket_start), {"duration_sum": 0.0, "timed": 0, "tokens": 0}
        )
        entry[row_outcome] = int(count)
        entry["duration_sum"] += float(duration_sum or 0)
        entry["timed"] += int(timed or 0)
        entry["tokens"] += int(tokens)

    first = int(start.timestamp()) // bucket * bucket
    last = int((end - timedelta(microseconds=1)).timestamp()) // bucket * bucket
    buckets = []
    for ts in range(first, last + 1, bucket):
        entry = per_bucket.get(ts, {})
        timed = entry.get("timed", 0)
        buckets.append(
            RequestLogBucket(
                time=datetime.fromtimestamp(ts, tz=timezone.utc),
                success=entry.get(REQUEST_LOG_STATUS_SUCCESS, 0),
                error=entry.get(REQUEST_LOG_STATUS_ERROR, 0),
                incomplete=entry.get(REQUEST_LOG_STATUS_INCOMPLETE, 0),
                avg_latency_ms=(
                    round(entry["duration_sum"] / timed, 1) if timed else None
                ),
                tokens=entry.get("tokens", 0),
            )
        )

    span = end - start
    previous_conditions = _conditions(session, user, ctx, windowed, window=False)
    previous_conditions += [D.created_at >= start - span, D.created_at < start]
    previous = await _totals(session, previous_conditions)

    return RequestLogStats(
        **totals.model_dump(),
        bucket_seconds=bucket,
        buckets=buckets,
        previous=previous,
    )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/request-logs", response_model=RequestLogList)
async def get_request_logs(
    session: SessionDep,
    user: CurrentUserDep,
    ctx: TenantContextDep,
    filters: RequestLogFiltersDep,
    page: Annotated[int, Query()] = 1,
    perPage: Annotated[int, Query()] = 25,
    sort_by: Annotated[
        Optional[str],
        Query(
            description="field or -field; one of " + ", ".join(REQUEST_LOG_SORT_FIELDS)
        ),
    ] = None,
):
    return await list_request_logs(
        session,
        user,
        ctx,
        filters,
        page=page,
        per_page=perPage,
        order_by=parse_sort(sort_by),
    )


@router.get("/request-logs/stats", response_model=RequestLogStats)
async def get_request_log_stats(
    session: SessionDep,
    user: CurrentUserDep,
    ctx: TenantContextDep,
    filters: RequestLogFiltersDep,
):
    return await request_log_stats(session, user, ctx, filters)


def parse_sort(sort_by: Optional[str]) -> Optional[List[Tuple[str, str]]]:
    if not sort_by:
        return None
    order: List[Tuple[str, str]] = []
    for part in sort_by.split(","):
        part = part.strip()
        if not part:
            continue
        direction = "desc" if part.startswith("-") else "asc"
        name = part.lstrip("-")
        if name not in REQUEST_LOG_SORT_FIELDS:
            raise InvalidException(
                message=f"Field '{name}' is not sortable. "
                f"Allowed fields: {', '.join(REQUEST_LOG_SORT_FIELDS)}"
            )
        order.append((name, direction))
    return order or None
