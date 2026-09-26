"""Response shapes for the per-request log under Usage.

The log is a read view over ``model_usage_details``: one row per inference
request reported by the gateway (or by the direct inference path when the
gateway is disabled). Nothing here is persisted.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from gpustack.schemas.common import PaginatedList

# Derived request outcome. ``status_code`` is authoritative when the reporter
# carried it; otherwise the outcome is derived from the columns every report
# has (see ``gpustack.routes.request_logs.outcome_expression``).
REQUEST_LOG_STATUS_SUCCESS = "success"
REQUEST_LOG_STATUS_ERROR = "error"
# The response ended before the usage report was observed: the client went
# away mid-response, or the upstream stopped mid-stream. Token counts on such
# a row are estimates.
REQUEST_LOG_STATUS_INCOMPLETE = "incomplete"
REQUEST_LOG_STATUSES = (
    REQUEST_LOG_STATUS_SUCCESS,
    REQUEST_LOG_STATUS_ERROR,
    REQUEST_LOG_STATUS_INCOMPLETE,
)

REQUEST_LOG_SORT_FIELDS = (
    "created_at",
    "duration_ms",
    "ttft_ms",
    "total_tokens",
)


class RequestLogItem(BaseModel):
    id: int
    # Envoy's ``x-request-id`` (the id the gateway echoes to the caller as
    # ``X-GPUStack-Request-Id``), and the model's own response id.
    request_id: Optional[str] = None
    upstream_response_id: Optional[str] = None

    status: str
    status_code: Optional[int] = None
    # Operation (``chat_completion`` / ``embedding`` / ...) when reported,
    # otherwise the route's first category (``llm`` / ``embedding`` / ...).
    type: Optional[str] = None
    stream: bool = False
    # False when the token counts are estimates (see ``completed`` on
    # ``ModelUsageDetails``).
    completed: bool = True

    model_name: str
    model_route_id: Optional[int] = None
    model_route_name: Optional[str] = None
    provider_name: Optional[str] = None
    cluster_name: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    api_key_id: Optional[int] = None
    api_key_name: Optional[str] = None
    access_key: Optional[str] = None
    user_agent: Optional[str] = None

    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    ttft_ms: Optional[int] = None

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    # Output tokens per second of generation: completion tokens over the time
    # after the first token (streams) or over the whole request (non-streams).
    tokens_per_second: Optional[float] = None

    model_config = ConfigDict(protected_namespaces=())


RequestLogList = PaginatedList[RequestLogItem]


class RequestLogBucket(BaseModel):
    # Bucket start, UTC.
    time: datetime
    success: int = 0
    error: int = 0
    incomplete: int = 0
    # Mean request duration of the bucket's timed rows; NULL when none.
    avg_latency_ms: Optional[float] = None
    # Prompt + completion tokens of the bucket's rows.
    tokens: int = 0


class RequestLogTotals(BaseModel):
    total_requests: int = 0
    success_requests: int = 0
    error_requests: int = 0
    incomplete_requests: int = 0
    # Share of rows that succeeded.
    success_rate: Optional[float] = None
    # Share of caller requests that succeeded. A request the gateway retried
    # on a fallback target writes one row per attempt under a single
    # ``request_id``; here it counts once, as a success if any attempt was.
    user_success_rate: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    p95_latency_ms: Optional[float] = None
    avg_ttft_ms: Optional[float] = None
    avg_tokens_per_second: Optional[float] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class RequestLogStats(RequestLogTotals):
    bucket_seconds: int
    buckets: List[RequestLogBucket]
    # The same totals over the equally long window right before the requested
    # one, for trend indicators. ``None`` when the window is open-ended.
    previous: Optional[RequestLogTotals] = None
