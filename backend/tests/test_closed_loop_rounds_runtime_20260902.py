"""RED tests for closed-loop runtime-adaptive max_rounds selection.

All six tests must fail against the current production code because the
runtime-aware ``max_rounds`` selection and ``max_rounds_source`` audit field
have not been implemented yet.
"""

import asyncio
import os

import pytest

from app.core.config import settings
from app.models.schemas import Entity
from app.services.closed_loop_recognition_20260902 import (
    ClosedLoopConfig,
    run_closed_loop_text,
)


def _entity(entity_type: str, text: str, start: int, *, source: str = "has", confidence=None, entity_id="raw"):
    return Entity(
        id=entity_id,
        text=text,
        type=entity_type,
        start=start,
        end=start + len(text),
        source=source,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# a. external runtime, no env/mapping override → max_rounds == 1
# ---------------------------------------------------------------------------
def test_external_runtime_no_override_max_rounds_is_one(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external", raising=False)
    monkeypatch.delenv("CLOSED_LOOP_MAX_ROUNDS", raising=False)

    cfg = ClosedLoopConfig.from_mapping()

    assert cfg.max_rounds == 1
    assert cfg.max_rounds_source == "runtime_adaptive"


# ---------------------------------------------------------------------------
# b. external runtime + env CLOSED_LOOP_MAX_ROUNDS=3 → max_rounds == 3
# ---------------------------------------------------------------------------
def test_external_runtime_env_override_max_rounds(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external", raising=False)
    monkeypatch.setenv("CLOSED_LOOP_MAX_ROUNDS", "3")

    cfg = ClosedLoopConfig.from_mapping()

    assert cfg.max_rounds == 3
    assert cfg.max_rounds_source == "env"


# ---------------------------------------------------------------------------
# c. HAS_TEXT_RUNTIME=llamacpp, no override → max_rounds == 3
# ---------------------------------------------------------------------------
def test_local_llamacpp_runtime_no_override_keeps_three_rounds(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "llamacpp", raising=False)
    monkeypatch.delenv("CLOSED_LOOP_MAX_ROUNDS", raising=False)

    cfg = ClosedLoopConfig.from_mapping()

    assert cfg.max_rounds == 3
    assert cfg.max_rounds_source == "runtime_adaptive"


# ---------------------------------------------------------------------------
# d. mapping {"closed_loop": {"max_rounds": 2}} → max_rounds == 2
# ---------------------------------------------------------------------------
def test_mapping_override_takes_precedence_over_runtime_default(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external", raising=False)
    monkeypatch.delenv("CLOSED_LOOP_MAX_ROUNDS", raising=False)

    cfg = ClosedLoopConfig.from_mapping({"closed_loop": {"max_rounds": 2}})

    assert cfg.max_rounds == 2
    assert cfg.max_rounds_source == "mapping"


# ---------------------------------------------------------------------------
# e. external runtime → run_closed_loop_text calls detector exactly once
# ---------------------------------------------------------------------------
def test_external_runtime_limits_closed_loop_to_single_detector_call(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external", raising=False)
    monkeypatch.delenv("CLOSED_LOOP_MAX_ROUNDS", raising=False)

    text = "姓名：张三；联系电话：13800000000"
    calls = []

    async def detector(current_text, _types):
        calls.append(current_text)
        return []

    cfg = ClosedLoopConfig.from_mapping()
    entities, audit = asyncio.run(
        run_closed_loop_text(text, [], detector=detector, config=cfg)
    )

    assert len(calls) == 1
    assert audit["max_rounds_source"] == "runtime_adaptive"


# ---------------------------------------------------------------------------
# f. audit payload records max_rounds_source for external no-override
# ---------------------------------------------------------------------------
def test_external_runtime_audit_records_max_rounds_source(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external", raising=False)
    monkeypatch.delenv("CLOSED_LOOP_MAX_ROUNDS", raising=False)

    text = "姓名：张三"
    calls = []

    async def detector(current_text, _types):
        calls.append(current_text)
        return [_entity("PERSON", "张三", text.index("张三"), source="has")]

    cfg = ClosedLoopConfig.from_mapping()
    entities, audit = asyncio.run(
        run_closed_loop_text(text, [], detector=detector, config=cfg)
    )

    assert audit["max_rounds_source"] == "runtime_adaptive"
