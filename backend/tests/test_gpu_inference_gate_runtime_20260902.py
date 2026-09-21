"""external runtime 旁路全局 GPU 推理闸门 — TDD RED 阶段。

4 个测试全部针对 planned behavior 编写，在当前代码上预期 FAIL。
"""

import asyncio
import threading
import time
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.gpu_inference_gate import shared_gpu_inference_slot
from app.services.has_service import HaSService


# ---------------------------------------------------------------------------
# 闸门进入计数器（把并发峰值观测插在闸门内部，避免 wrapper 自己的锁干扰）
# ---------------------------------------------------------------------------

class _CountingGate:
    """包裹 shared_gpu_inference_slot，在闸门内部统计并发峰值。"""

    def __init__(self) -> None:
        self.peak = 0
        self._current = 0

    @asynccontextmanager
    async def __call__(self, label: str):
        try:
            async with shared_gpu_inference_slot(label):
                # 在闸门内部计数：只有真正拿到 slot 才算进入
                self._current += 1
                self.peak = max(self.peak, self._current)
                yield
        finally:
            self._current -= 1


# ---------------------------------------------------------------------------
# HaS NER stub client（线程安全，供 asyncio.to_thread 使用）
# ---------------------------------------------------------------------------

class _CountingHaSClient:
    """记录 ner() 并发峰值与调用次数的替身。"""

    base_url = "http://stub:0"

    def __init__(self, hold_sec: float = 0.05):
        self.hold_sec = hold_sec
        self.calls = 0
        self.peak = 0
        self._current = 0
        self._lock = threading.Lock()

    def ner(self, text, entity_types=None, **kwargs):
        with self._lock:
            self.calls += 1
            self._current += 1
            self.peak = max(self.peak, self._current)
        try:
            time.sleep(self.hold_sec)
            return {"姓名": ["张三"]}
        finally:
            with self._lock:
                self._current -= 1


# ---------------------------------------------------------------------------
# 测试 1：runtime=external 且 SERIALIZE_SHARED_GPU_MODELS=True
#          → 两个并发持有者应同时进入（旁路闸门，峰值 == 2）
# ---------------------------------------------------------------------------

def test_external_runtime_bypasses_global_gate(monkeypatch):
    """external runtime 应旁路全局 GPU 推理闸门，允许并发峰值 == 2。"""
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external")
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", True)
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 1, raising=False)

    counter = _CountingGate()

    async def run():
        await asyncio.gather(
            _enter_and_hold(counter, 0.1),
            _enter_and_hold(counter, 0.1),
        )

    asyncio.run(run())
    assert counter.peak == 2, (
        f"runtime=external 应旁路闸门，并发峰值应为 2，实际 {counter.peak}"
    )


async def _enter_and_hold(counter: _CountingGate, hold_sec: float) -> None:
    async with counter("test-external-bypass"):
        await asyncio.sleep(hold_sec)


# ---------------------------------------------------------------------------
# 测试 2：runtime=llamacpp 且 HAS_NER_GLOBAL_MAX_INFLIGHT=1
#          → 两个并发持有者串行（峰值 == 1，钉住现有行为）
# ---------------------------------------------------------------------------

def test_llamacpp_runtime_respects_global_gate(monkeypatch):
    """llamacpp runtime 应遵守全局闸门，并发峰值 == 1。"""
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "llamacpp")
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", True)
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 1, raising=False)

    counter = _CountingGate()

    async def run():
        await asyncio.gather(
            _enter_and_hold(counter, 0.1),
            _enter_and_hold(counter, 0.1),
        )

    asyncio.run(run())
    assert counter.peak == 1, (
        f"runtime=llamacpp 应遵守闸门，并发峰值应为 1，实际 {counter.peak}"
    )


# ---------------------------------------------------------------------------
# 测试 3：runtime=external 但 kill-switch HAS_NER_EXTERNAL_GATE_BYPASS=False
#          → 闸门重新生效（峰值 == 1）
# ---------------------------------------------------------------------------

def test_external_runtime_killswitch_disables_bypass(monkeypatch):
    """HAS_NER_EXTERNAL_GATE_BYPASS=False 时，即使 runtime=external 也应重新启用闸门。"""
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external")
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", True)
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 1, raising=False)
    # HAS_NER_EXTERNAL_GATE_BYPASS 是计划中的 kill-switch，当前 settings 尚无此字段；
    # 直接注入实例属性以模拟“已配置但未实现”的状态。
    object.__setattr__(settings, "HAS_NER_EXTERNAL_GATE_BYPASS", False)

    counter = _CountingGate()

    async def run():
        await asyncio.gather(
            _enter_and_hold(counter, 0.1),
            _enter_and_hold(counter, 0.1),
        )

    asyncio.run(run())
    assert counter.peak == 1, (
        f"HAS_NER_EXTERNAL_GATE_BYPASS=False 时应重新启用闸门，"
        f"并发峰值应为 1，实际 {counter.peak}"
    )


# ---------------------------------------------------------------------------
# 测试 4：HaSService.extract_entities + stub client
#          runtime=external、HAS_NER_MAX_PARALLEL_REQUESTS=4、4 个不同类型
#          → 观测并发峰值 == 4（今天会被闸门压到 1）
# ---------------------------------------------------------------------------

class _NameType:
    id = "PERSON"
    name = "姓名"


class _AddressType:
    id = "ADDRESS"
    name = "地址"


class _PhoneType:
    id = "PHONE"
    name = "电话"


class _DateType:
    id = "BIRTH_DATE"
    name = "出生日期"


def test_has_extract_entities_external_runtime_allows_parallel(monkeypatch):
    """runtime=external 时，HaSService.extract_entities 应允许 4 路并行 NER。"""
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external")
    monkeypatch.setattr(settings, "HAS_NER_MAX_PARALLEL_REQUESTS", 4)
    monkeypatch.setattr(settings, "HAS_NER_GLOBAL_MAX_INFLIGHT", 1, raising=False)
    monkeypatch.setattr(settings, "SERIALIZE_SHARED_GPU_MODELS", True)

    client = _CountingHaSClient(hold_sec=0.1)
    service = HaSService()
    service.client = client

    entity_types = [_NameType(), _AddressType(), _PhoneType(), _DateType()]

    asyncio.run(
        service.extract_entities("张三 北京市 13800000000 1990年1月1日", entity_types)
    )

    assert client.peak == 4, (
        f"runtime=external + HAS_NER_MAX_PARALLEL_REQUESTS=4 时，"
        f"并发峰值应为 4，实际 {client.peak}"
    )
