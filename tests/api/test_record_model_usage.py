"""What the direct (non-gateway) inference path reports per request.

``record_model_usage`` is only reached for responses that went out as 200,
so it can state the status outright, and it knows whether the caller asked
for a stream and when the first chunk left.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gpustack.api import middlewares
from gpustack.schemas.model_usage import OperationEnum

START = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)


def _request(headers=None, **state):
    return SimpleNamespace(
        state=SimpleNamespace(
            user=SimpleNamespace(id=7),
            model=SimpleNamespace(id=1, name="qwen3", cluster_id=2),
            api_key=SimpleNamespace(access_key="ak"),
            start_time=START,
            **state,
        ),
        headers=headers or {},
    )


@pytest.fixture
def reported(monkeypatch):
    captured = []

    async def _accumulate(metrics):
        captured.extend(metrics)

    monkeypatch.setattr(middlewares, "accumulate_gateway_metrics", _accumulate)
    return captured


@pytest.mark.asyncio
async def test_stream_reports_status_stream_and_ttft(reported):
    request = _request(
        stream=True, first_token_time=START + timedelta(milliseconds=250)
    )
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8)

    await middlewares.record_model_usage(request, usage, OperationEnum.CHAT_COMPLETION)

    (metric,) = reported
    assert metric.status_code == 200
    assert metric.stream is True
    assert metric.ttft_ms == 250


@pytest.mark.asyncio
async def test_non_stream_reports_no_ttft(reported):
    await middlewares.record_model_usage(_request(), None, OperationEnum.EMBEDDING)

    (metric,) = reported
    assert metric.status_code == 200
    assert metric.stream is False
    assert metric.ttft_ms is None


@pytest.mark.asyncio
async def test_reports_the_callers_user_agent(reported):
    await middlewares.record_model_usage(
        _request(headers={"user-agent": "openai-python/1.51.0"}),
        None,
        OperationEnum.CHAT_COMPLETION,
    )

    (metric,) = reported
    assert metric.user_agent == "openai-python/1.51.0"
