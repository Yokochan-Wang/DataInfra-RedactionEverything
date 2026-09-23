"""Residual-round input (FR-04) and FR-03 hard-pruning tests."""

import asyncio

from app.models.schemas import Entity
from app.services.closed_loop_recognition_20260902 import (
    CandidateRecord,
    ClosedLoopConfig,
    build_pruning_plan,
    build_residual_input,
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


def test_hard_pruning_accepts_two_consistent_evidence_kinds():
    text = "签约方：某某科技有限公司；电话：13800000000。"
    start = text.index("某某科技有限公司")
    org = _entity("ORG", "某某科技有限公司", start)
    single = CandidateRecord(entity=org, rounds=[1], sources={"has"}, evidence_count=1)
    agreed = CandidateRecord(
        entity=org.model_copy(deep=True),
        rounds=[1],
        sources={"has", "regex"},
        evidence_count=2,
    )

    soft_plan = build_pruning_plan(text, [single], ClosedLoopConfig())
    hard_plan = build_pruning_plan(text, [agreed], ClosedLoopConfig())

    assert soft_plan.hard_ranges == ()
    assert soft_plan.soft_ranges == ((start, start + len("某某科技有限公司")),)
    assert hard_plan.hard_ranges == ((start, start + len("某某科技有限公司")),)
    assert hard_plan.soft_ranges == ()


def test_identity_type_still_requires_the_hard_threshold():
    text = "签约：张三，电话：13800000000。"
    start = text.index("张三")
    person = _entity("PERSON", "张三", start)
    record = CandidateRecord(entity=person, rounds=[1], sources={"has", "regex"}, evidence_count=2)

    plan = build_pruning_plan(text, [record], ClosedLoopConfig())

    assert plan.hard_ranges == ()
    assert plan.soft_ranges == ((start, start + 2),)


def test_residual_input_removes_hard_spans_and_keeps_offsets():
    text = "电话：13800000000，备用：13900000000。"
    phone = _entity("PHONE", "13800000000", text.index("13800000000"), source="regex")
    contact = _entity("ORG", "电话", text.index("电话"))
    records = [
        CandidateRecord(entity=phone, rounds=[1], sources={"regex"}, evidence_count=1),
        CandidateRecord(entity=contact, rounds=[1], sources={"has"}, evidence_count=1),
    ]

    plan = build_pruning_plan(text, records, ClosedLoopConfig())
    residual = build_residual_input(text, plan, ClosedLoopConfig())

    assert plan.hard_ranges == ((text.index("13800000000"), text.index("13800000000") + 11),)
    assert "13800000000" not in residual.text
    assert "电话" not in residual.text
    assert "<PROTECTED_ENTITY_1>" in residual.text
    assert residual.residual_chars < len(text)
    assert len(residual.offsets) == len(residual.text)
    assert residual.offsets[0] == text.index("电话")
    assert residual.offsets[residual.text.index("：")] == text.index("：")
    kept = residual.text.index("，备用：")
    assert residual.offsets[kept] == text.index("，备用：")


def test_round_two_sends_only_residual_text_and_reopens_adjacent_evidence():
    text = "电话：13800000000，备用：13900000000。"
    calls: list[str] = []

    async def detector(current_text, _types):
        calls.append(current_text)
        if len(calls) == 1:
            start = current_text.index("13800000000")
            return [_entity("PHONE", "13800000000", start, source="regex")]
        if len(calls) == 2:
            return [_entity("ORG", "电话：", 0)]
        return []

    entities, audit = asyncio.run(
        run_closed_loop_text(text, [], detector=detector, config=ClosedLoopConfig())
    )

    assert len(calls) == 3
    assert "13800000000" not in calls[1]
    assert len(calls[1]) < len(text)
    assert audit["pruning_summary"]["reopened_ranges"] >= 1
    assert audit["rounds"][1]["next_round_input"]["reopened_ranges"] == 1
    assert "13800000000" in calls[2]
    assert {"电话：", "13800000000"} <= {entity.text for entity in entities}


def test_review_only_rounds_stop_early_instead_of_repeating_the_same_payload():
    text = "甲：张三；乙"
    calls: list[str] = []

    async def detector(current_text, _types):
        calls.append(current_text)
        return [_entity("ORG", "张三", current_text.index("张三"), confidence=0.2)]

    _, audit = asyncio.run(
        run_closed_loop_text(text, [], detector=detector, config=ClosedLoopConfig(max_rounds=3))
    )

    assert len(calls) == 1
    assert audit["rounds_run"] == 1
    assert audit["termination_reason"] == "converged_identical_input"

    repeated: list[str] = []

    async def repeat_detector(current_text, _types):
        repeated.append(current_text)
        return [_entity("ORG", "张三", current_text.index("张三"), confidence=0.2)]

    _, repeat_audit = asyncio.run(
        run_closed_loop_text(
            text,
            [],
            detector=repeat_detector,
            config=ClosedLoopConfig(max_rounds=3, skip_identical_input=False),
        )
    )

    assert len(repeated) == 2
    assert repeat_audit["termination_reason"] == "converged_no_new_candidates"
