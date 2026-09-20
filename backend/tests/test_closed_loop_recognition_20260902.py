"""Pure tests for the dated three-round closed-loop recognizer."""

import asyncio

from app.models.schemas import Entity
from app.services.closed_loop_recognition_20260902 import (
    CandidateRecord,
    ClosedLoopConfig,
    build_pruning_plan,
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


def test_three_round_loop_masks_previous_spans_and_recovers_residual_value():
    text = "姓名：张三；联系电话：13800000000；地址：上海市浦东新区"
    calls: list[str] = []

    async def detector(current_text, _types):
        calls.append(current_text)
        if len(calls) == 1:
            return [_entity("PERSON", "张三", text.index("张三"), source="has")]
        if len(calls) == 2:
            return [_entity("PHONE", "13800000000", text.index("13800000000"), source="regex")]
        return []

    entities, audit = asyncio.run(
        run_closed_loop_text(
            text,
            [],
            detector=detector,
            config=ClosedLoopConfig(max_rounds=3, min_new_candidates_to_continue=1),
        )
    )

    assert {entity.text for entity in entities} == {"张三", "13800000000"}
    assert len(calls) == 3
    assert "张三" not in calls[1]
    assert "13800000000" in calls[1]
    assert audit["rounds_run"] == 3
    assert audit["termination_reason"] == "converged_no_new_candidates"
    assert audit["pruning_summary"]["masked_chars"] >= len("张三")


def test_hard_pruning_requires_deterministic_evidence_and_soft_keeps_audit():
    text = "联系人：张三；电话：13800000000。"
    regex_entity = _entity("PHONE", "13800000000", text.index("13800000000"), source="regex")
    has_entity = _entity("PERSON", "张三", text.index("张三"), source="has")
    records = [
        CandidateRecord(
            entity=regex_entity,
            rounds=[1],
            sources={"regex"},
            evidence_count=1,
        ),
        CandidateRecord(
            entity=has_entity,
            rounds=[1],
            sources={"has"},
            evidence_count=1,
        ),
    ]

    plan = build_pruning_plan(text, records, ClosedLoopConfig())

    assert plan.hard_ranges == ((text.index("13800000000"), text.index("13800000000") + 11),)
    assert plan.soft_ranges == ((text.index("张三"), text.index("张三") + 2),)
    assert plan.review_ranges == ()
    assert "13800000000" not in plan.masked_text
    assert "张三" not in plan.masked_text


def test_review_range_is_not_masked_for_next_round():
    text = "甲：张三；乙"
    candidate = _entity("ORG", "张三", 2, source="has", confidence=0.2)
    record = CandidateRecord(entity=candidate, rounds=[1], sources={"has"}, evidence_count=1)

    plan = build_pruning_plan(text, [record], ClosedLoopConfig())

    assert plan.review_ranges == ((2, 4),)
    assert plan.hard_ranges == ()
    assert plan.soft_ranges == ()
    assert plan.masked_text == text
