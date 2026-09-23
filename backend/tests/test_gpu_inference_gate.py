"""shared_gpu_inference_slot 并发闸门语义。

背景：原实现是进程级 degree-1 asyncio.Lock，把 vLLM 双实例的 HaS NER
压成全局串行（2026-07 万级批量吞吐优化 P0）。改为 loop-aware 可配置
Semaphore：默认 1 = 原行为；HAS_NER_GLOBAL_MAX_INFLIGHT 放开并发；
SERIALIZE_SHARED_GPU_MODELS=False 完全旁路。

2026-09-21：闸门新增 runtime 感知——HAS_TEXT_RUNTIME=external（远端服务，
无本地 GPU 争用）时自动旁路（kill-switch: HAS_NER_EXTERNAL_GATE_BYPASS）。
本文件测试的是信号量机制本身，与 runtime 策略无关；root .env 的
HAS_TEXT_RUNTIME=external 会命中旁路，因此这些测试显式钉住本地 runtime，
语义与改动前完全一致（旁路行为由
test_gpu_inference_gate_runtime_20260902.py 覆盖）。
"""

import asyncio

from app.core import gpu_inference_gate as gate
from app.core.config import settings

_LOCAL_RUNTIME = "llamacpp"


async def _measure_peak(workers: int, hold_sec: float = 0.02) -> int:
    peak = 0
    current = 0

    async def worker() -> None:
        nonlocal peak, current
        async with gate.shared_gpu_inference_slot("test"):
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(hold_sec)
            current -= 1

    await asyncio.gather(*(worker() for _ in range(workers)))
    return peak


def test_default_serializes(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", _LOCAL_RUNTIME)
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", True)
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 1, raising=False)
    assert asyncio.run(_measure_peak(8)) == 1


def test_configured_inflight(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", _LOCAL_RUNTIME)
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", True)
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 4, raising=False)
    assert asyncio.run(_measure_peak(8)) == 4


def test_gate_bypass(monkeypatch):
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", False)
    assert asyncio.run(_measure_peak(8)) == 8


def test_new_loop_rebinds_semaphore(monkeypatch):
    """跨 asyncio.run（新事件循环）闸门必须重建，不复用旧 loop 的信号量。"""
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", _LOCAL_RUNTIME)
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", True)
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 2, raising=False)
    assert asyncio.run(_measure_peak(6)) == 2
    assert asyncio.run(_measure_peak(6)) == 2


def test_config_change_rebuilds_gate(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", _LOCAL_RUNTIME)
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", True)
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 1, raising=False)
    assert asyncio.run(_measure_peak(6)) == 1
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 3, raising=False)
    assert asyncio.run(_measure_peak(6)) == 3


def test_validator_clamps():
    settings_cls = type(settings)
    assert settings_cls(HAS_NER_GLOBAL_MAX_INFLIGHT=99).HAS_NER_GLOBAL_MAX_INFLIGHT == 12
    assert settings_cls(HAS_NER_GLOBAL_MAX_INFLIGHT=0).HAS_NER_GLOBAL_MAX_INFLIGHT == 1

