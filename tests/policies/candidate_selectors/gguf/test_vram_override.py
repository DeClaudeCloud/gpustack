"""Explicit GGUF reservations affect only the single selected GPU estimate."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gpustack.policies.candidate_selectors import GGUFResourceFitSelector


def selector(value=None, selected=None, gpu_count=1):
    item = SimpleNamespace(
        offloadLayers=64,
        vrams=[SimpleNamespace(uma=300, nonuma=400) for _ in range(gpu_count)],
        ram=SimpleNamespace(uma=100, nonuma=200),
    )
    result = SimpleNamespace(resource_claim_estimate=SimpleNamespace(items=[item]))
    instance = object.__new__(GGUFResourceFitSelector)
    instance._model = SimpleNamespace(
        name="test", env={} if value is None else {"GPUSTACK_MODEL_VRAM_CLAIM": value}
    )
    instance._selected_gpu_ids = selected or []
    instance._calculate_model_resource_claim = AsyncMock(return_value=result)
    instance._set_single_layer_vram = Mock()
    return instance, result


@pytest.mark.asyncio
async def test_override_preserves_ram_and_parser_estimates():
    instance, result = selector("96636764160", ["worker:cuda:0"])
    original = copy.deepcopy(result)
    await instance._set_offload_resource_claim()
    item = instance._full_offload_resource_claim.items[0]
    assert item.vrams[0].nonuma == 96636764160
    assert item.vrams[0].uma == 96636764160
    assert item.ram == original.resource_claim_estimate.items[0].ram
    assert instance._non_uma_single_gpu_full_offload_vram == 96636764160
    assert instance._uma_single_gpu_full_offload_vram == 96636764160
    assert result == original
    assert instance._partial_offload_resource_claim == result.resource_claim_estimate
    instance._set_single_layer_vram.assert_called_once()


@pytest.mark.asyncio
async def test_no_override_keeps_calculated_claim_without_gpu_selection():
    instance, _ = selector()
    await instance._set_offload_resource_claim()
    assert instance._non_uma_single_gpu_full_offload_vram == 400
    assert instance._uma_single_gpu_full_offload_vram == 300


@pytest.mark.parametrize("value", ["0", "-1", "", "abc", "1.5"])
@pytest.mark.asyncio
async def test_invalid_override_is_rejected(value):
    instance, _ = selector(value, ["worker:cuda:0"])
    with pytest.raises(ValueError, match="positive integer in bytes"):
        await instance._set_offload_resource_claim()


@pytest.mark.parametrize(
    "selected,gpu_count",
    [([], 1), (["worker:cuda:0", "worker:cuda:1"], 1), (["worker:cuda:0"], 2)],
)
@pytest.mark.asyncio
async def test_override_requires_one_selected_gpu_and_one_estimated_gpu(
    selected, gpu_count
):
    instance, _ = selector("90", selected, gpu_count)
    with pytest.raises(ValueError, match="exactly one manually selected GPU"):
        await instance._set_offload_resource_claim()


@pytest.mark.parametrize(
    "vram,ram,expected", [(95, 184, True), (89, 184, False), (95, 128, False)]
)
@pytest.mark.asyncio
async def test_candidate_checks_override_and_calculated_ram(vram, ram, expected):
    gib = 1024**3
    instance, result = selector(str(90 * gib), ["worker:cuda:0"])
    result.resource_claim_estimate.items[0].vrams[0].nonuma = 257 * gib
    result.resource_claim_estimate.items[0].ram.nonuma = 129 * gib
    await instance._set_offload_resource_claim()
    worker = SimpleNamespace(
        id=1, status=SimpleNamespace(memory=SimpleNamespace(is_unified_memory=False))
    )
    instance._worker_id_to_worker = {1: worker}
    instance._workers_allocatable_resource = {1: SimpleNamespace(ram=ram * gib)}
    instance._create_candidate = Mock()
    candidates, _ = (
        await instance._find_single_worker_single_gpu_full_offloading_candidates(
            (1, 0, vram * gib)
        )
    )
    assert bool(candidates) is expected
    if expected:
        instance._create_candidate.assert_called_once_with(
            worker, 64, 129 * gib, {0: 90 * gib}, [0]
        )
    else:
        instance._create_candidate.assert_not_called()
