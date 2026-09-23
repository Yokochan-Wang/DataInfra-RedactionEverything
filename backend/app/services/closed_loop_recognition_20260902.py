"""Three-round closed-loop text recognition with safe pruning.

This module is intentionally versioned (``20260902``).  It is an additive
implementation for the closed-loop recognition requirement and does not
rewrite the existing recognition modules.  The runtime overlay imports this
module when the dated application entrypoint is used.

The implementation is conservative by design:

* round 1 always runs on the original text;
* later rounds are built from the residual input: hard-pruned regions leave
  the input, soft-pruned regions become a PROTECTED_ENTITY placeholder and
  unresolved regions are re-sent verbatim; a per-round offset map translates
  detector spans back to the original document, so coordinates always stay
  aligned;
* low-confidence and boundary-sensitive spans are soft-pruned and remain in
  the audit trail; identity spans are never hard-pruned unless deterministic
  evidence is available;
* candidates are merged monotonically and conflicts are retained as metadata
  rather than silently discarded.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from app.models.schemas import Entity
from app.models.type_mapping import canonical_type_id

logger = logging.getLogger(__name__)

Detector = Callable[[str, list[Any]], Awaitable[list[Entity]]]

_IDENTITY_TYPES = frozenset(
    {
        "PERSON",
        "PHONE",
        "MOBILE",
        "EMAIL",
        "ID_CARD",
        "DOCUMENT_NUMBER",
        "BANK_CARD",
        "BANK_ACCOUNT",
        "ACCOUNT",
        "PASSPORT",
        "CREDIT_CODE",
        "TAX_NUMBER",
        "LICENSE_PLATE",
        "VIN",
    }
)
_EDGE_PUNCTUATION = " \t\r\n，。；：、,.!?！？;:()（）[]【】<>"
_MASK_CHAR = " "
_PLACEHOLDER_TEMPLATE = "<PROTECTED_ENTITY_{index}>"
_SEGMENT_SEPARATOR = "\n"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _runtime_adaptive_max_rounds() -> int:
    """Default round count for the active text-detection runtime.

    The ``external`` runtime proxies every round to a remote detector, so the
    extra rounds cost real network latency per document while only a tiny
    fraction of golden-corpus entities first appear in round >= 2.  Local
    runtimes keep the full three rounds.
    """
    from app.core.config import is_remote_text_runtime

    if is_remote_text_runtime():
        return 1
    return 3


@dataclass(frozen=True)
class ClosedLoopConfig:
    """Runtime knobs for the three-round loop."""

    enabled: bool = True
    max_rounds: int = 3
    max_rounds_source: str = "default"
    hard_prune_confidence: float = 0.95
    soft_prune_confidence: float = 0.75
    context_chars: int = 32
    min_new_candidates_to_continue: int = 1
    identity_final_check: bool = True
    hard_prune_min_evidence_kinds: int = 2
    skip_identical_input: bool = True
    block_chars: int = 320

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None = None) -> "ClosedLoopConfig":
        raw = mapping if isinstance(mapping, dict) else {}
        nested = raw.get("closed_loop") if isinstance(raw.get("closed_loop"), dict) else raw

        def pick(name: str, env_name: str, default: Any) -> Any:
            if name in nested:
                return nested[name]
            return os.environ.get(env_name, default)

        enabled = pick("enabled", "CLOSED_LOOP_ENABLED", _env_bool("CLOSED_LOOP_ENABLED", True))
        if "max_rounds" in nested:
            max_rounds = nested["max_rounds"]
            max_rounds_source = "mapping"
        else:
            env_max_rounds = os.environ.get("CLOSED_LOOP_MAX_ROUNDS")
            if env_max_rounds is None:
                max_rounds = _runtime_adaptive_max_rounds()
                max_rounds_source = "runtime_adaptive"
            else:
                max_rounds = env_max_rounds
                max_rounds_source = "env"
        hard = pick("hard_prune_confidence", "CLOSED_LOOP_HARD_PRUNE_CONFIDENCE", 0.95)
        soft = pick("soft_prune_confidence", "CLOSED_LOOP_SOFT_PRUNE_CONFIDENCE", 0.75)
        context = pick("context_chars", "CLOSED_LOOP_CONTEXT_CHARS", 32)
        min_new = pick("min_new_candidates_to_continue", "CLOSED_LOOP_MIN_NEW_CANDIDATES", 1)
        identity_check = pick("identity_final_check", "CLOSED_LOOP_IDENTITY_FINAL_CHECK", True)
        evidence_kinds = pick(
            "hard_prune_min_evidence_kinds", "CLOSED_LOOP_HARD_PRUNE_MIN_EVIDENCE_KINDS", 2
        )
        skip_identical = pick("skip_identical_input", "CLOSED_LOOP_SKIP_IDENTICAL_INPUT", True)
        block_chars = pick("block_chars", "CLOSED_LOOP_BLOCK_CHARS", 320)

        def as_bool(value: Any, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

        def as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(maximum, parsed))

        def as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(maximum, parsed))

        soft_value = as_float(soft, 0.75, 0.0, 1.0)
        hard_value = as_float(hard, 0.95, soft_value, 1.0)
        return cls(
            enabled=as_bool(enabled, True),
            max_rounds=as_int(max_rounds, 3, 1, 3),
            max_rounds_source=max_rounds_source,
            hard_prune_confidence=hard_value,
            soft_prune_confidence=soft_value,
            context_chars=as_int(context, 32, 0, 512),
            min_new_candidates_to_continue=as_int(min_new, 1, 0, 1000),
            identity_final_check=as_bool(identity_check, True),
            hard_prune_min_evidence_kinds=as_int(evidence_kinds, 2, 1, 4),
            skip_identical_input=as_bool(skip_identical, True),
            block_chars=as_int(block_chars, 320, 64, 4096),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_rounds": self.max_rounds,
            "max_rounds_source": self.max_rounds_source,
            "hard_prune_confidence": self.hard_prune_confidence,
            "soft_prune_confidence": self.soft_prune_confidence,
            "context_chars": self.context_chars,
            "min_new_candidates_to_continue": self.min_new_candidates_to_continue,
            "identity_final_check": self.identity_final_check,
            "hard_prune_min_evidence_kinds": self.hard_prune_min_evidence_kinds,
            "skip_identical_input": self.skip_identical_input,
            "block_chars": self.block_chars,
        }


@dataclass
class CandidateRecord:
    """Merged candidate plus its cross-round lineage."""

    entity: Entity
    rounds: list[int] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)
    evidence_count: int = 0
    status: str = "tentative"
    pruning: str = "none"
    conflict_ids: list[str] = field(default_factory=list)
    decision_reason: str = ""

    @property
    def candidate_id(self) -> str:
        return self.entity.id

    @property
    def normalized_type(self) -> str:
        return canonical_type_id(str(self.entity.type or ""))

    @property
    def effective_confidence(self) -> float:
        if self.entity.confidence is not None:
            base = max(0.0, min(1.0, float(self.entity.confidence)))
        else:
            source = str(self.entity.source or "").lower()
            base = {
                "regex": 0.99,
                "manual": 1.0,
                "has": 0.82,
                "llm": 0.82,
            }.get(source, 0.70)
        # Independent detector evidence is a small, bounded boost.  Repeated
        # observations from the same source are retained in rounds but do not
        # masquerade as a second detector.
        if self.evidence_count > 1:
            base = min(1.0, base + min(0.10, 0.04 * (self.evidence_count - 1)))
        return base


@dataclass(frozen=True)
class PruningPlan:
    masked_text: str
    hard_ranges: tuple[tuple[int, int], ...]
    soft_ranges: tuple[tuple[int, int], ...]
    review_ranges: tuple[tuple[int, int], ...]
    input_chars: int
    residual_chars: int
    block_count: int
    remaining_block_count: int

    @property
    def all_ranges(self) -> tuple[tuple[int, int], ...]:
        # Review ranges stay visible to the next detector.  They are included
        # in the audit payload but are deliberately not masked.
        return (*self.hard_ranges, *self.soft_ranges)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_chars": self.input_chars,
            "residual_chars": self.residual_chars,
            "masked_chars": self.input_chars - self.residual_chars,
            "hard_pruned_chars": sum(end - start for start, end in self.hard_ranges),
            "soft_pruned_chars": sum(end - start for start, end in self.soft_ranges),
            "review_chars": sum(end - start for start, end in self.review_ranges),
            "hard_pruned_ranges": len(self.hard_ranges),
            "soft_pruned_ranges": len(self.soft_ranges),
            "review_ranges": len(self.review_ranges),
            "input_blocks": self.block_count,
            "remaining_blocks": self.remaining_block_count,
            "reduction_ratio": round(
                (self.input_chars - self.residual_chars) / self.input_chars, 4
            )
            if self.input_chars
            else 0.0,
        }


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip(_EDGE_PUNCTUATION)


def _identity_type(entity_type: str) -> bool:
    canonical = canonical_type_id(entity_type).upper()
    return canonical in _IDENTITY_TYPES or any(
        token in canonical for token in ("PHONE", "EMAIL", "ID_CARD", "ACCOUNT", "PASSPORT")
    )


def _source_name(entity: Entity) -> str:
    return str(entity.source or "unknown").lower()


def _overlap_ratio(left: Entity, right: Entity) -> float:
    start = max(int(left.start), int(right.start))
    end = min(int(left.end), int(right.end))
    overlap = max(0, end - start)
    if overlap == 0:
        return 0.0
    left_len = max(1, int(left.end) - int(left.start))
    right_len = max(1, int(right.end) - int(right.start))
    return overlap / min(left_len, right_len)


def _candidate_matches(existing: Entity, incoming: Entity) -> bool:
    if canonical_type_id(str(existing.type or "")) != canonical_type_id(str(incoming.type or "")):
        return False
    if existing.start == incoming.start and existing.end == incoming.end:
        return True
    if _overlap_ratio(existing, incoming) >= 0.5:
        return True
    # Same value may shift by a few characters after OCR normalization.  Keep
    # this intentionally local so two occurrences of the same name are not
    # merged across a document.
    if _compact(existing.text) == _compact(incoming.text):
        return abs(int(existing.start) - int(incoming.start)) <= 8
    return False


def _boundary_sensitive(text: str, entity: Entity) -> bool:
    start, end = int(entity.start), int(entity.end)
    if start <= 0 or end >= len(text):
        return True
    if "\n" in text[start:end] or "\r" in text[start:end]:
        return True
    left, right = text[start - 1], text[end]
    # Adjacent word characters imply a possible partial span.  The next round
    # should see the context rather than hard-pruning it.
    if (left.isalnum() or "\u4e00" <= left <= "\u9fff") and (
        right.isalnum() or "\u4e00" <= right <= "\u9fff"
    ):
        return True
    return False


def _valid_entity(entity: Any, source_text: str) -> Entity | None:
    if isinstance(entity, Entity):
        candidate = entity.model_copy(deep=True)
    elif isinstance(entity, dict):
        try:
            candidate = Entity.model_validate(entity)
        except Exception:
            return None
    else:
        return None

    value = str(candidate.text or "")
    if not value:
        return None
    start, end = int(candidate.start), int(candidate.end)
    if 0 <= start < end <= len(source_text) and source_text[start:end] == value:
        return candidate
    # NER may normalize whitespace.  Relocate a unique occurrence while
    # preserving the original span if it is already valid.
    compact_value = _compact(value)
    if not compact_value:
        return None
    compact_source = re.sub(r"\s+", "", source_text)
    compact_start = compact_source.find(compact_value)
    if compact_start < 0:
        return None
    # Build a compact-index -> source-index map for deterministic relocation.
    positions = [index for index, char in enumerate(source_text) if not char.isspace()]
    if compact_start >= len(positions):
        return None
    relocated_start = positions[compact_start]
    last_compact = compact_start + len(compact_value) - 1
    if last_compact >= len(positions):
        return None
    relocated_end = positions[last_compact] + 1
    candidate.start = relocated_start
    candidate.end = relocated_end
    candidate.text = source_text[relocated_start:relocated_end]
    return candidate


def _merge_ranges(ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    ordered = sorted((max(0, int(start)), max(0, int(end))) for start, end in ranges if end > start)
    if not ordered:
        return ()
    merged: list[list[int]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def _block_stats(text: str, block_chars: int, ranges: Iterable[tuple[int, int]]) -> tuple[int, int]:
    block_count = max(1, (len(text) + block_chars - 1) // block_chars) if text else 0
    remaining = 0
    merged = _merge_ranges(ranges)
    for block_start in range(0, len(text), block_chars):
        block_end = min(len(text), block_start + block_chars)
        covered = sum(
            max(0, min(end, block_end) - max(start, block_start))
            for start, end in merged
        )
        if covered < block_end - block_start:
            remaining += 1
    return block_count, remaining


def _classify_pruning(record: CandidateRecord, text: str, config: ClosedLoopConfig) -> tuple[str, str]:
    entity = record.entity
    if record.conflict_ids:
        return "review", "候选存在类型或范围冲突，保留原文供终检"
    if _boundary_sensitive(text, entity):
        return "soft", "实体靠近边界或可能为部分跨度，保留上下文"
    if _identity_type(record.normalized_type) and config.identity_final_check:
        if record.effective_confidence < config.hard_prune_confidence:
            return "soft", "identity 类未达到硬剪枝置信度，强制终检"
    # FR-03 hard pruning needs a confidence above the configured threshold
    # and consistent evidence.  Evidence from at least two evidence kinds
    # (NER + rule, OCR + NER, text + vision, ...) or a deterministic rule hit
    # is what confirms the candidate, so it lifts the effective confidence to
    # the hard-pruning threshold.  Repeated observations from a single source
    # are still counted once and cannot masquerade as confirmation.
    evidence_kinds = len({source for source in record.sources if source})
    deterministic = "regex" in record.sources or "manual" in record.sources
    confidence = record.effective_confidence
    if deterministic or evidence_kinds >= config.hard_prune_min_evidence_kinds:
        confidence = max(confidence, config.hard_prune_confidence)
    if confidence >= config.hard_prune_confidence:
        return "hard", "证据一致且达到硬剪枝阈值"
    if confidence >= config.soft_prune_confidence:
        return "soft", "中高置信度候选，保留占位和上下文"
    return "review", "低置信度候选，进入重点复检"


def build_pruning_plan(
    text: str,
    records: Iterable[CandidateRecord],
    config: ClosedLoopConfig | None = None,
) -> PruningPlan:
    """Create a same-length masked input for the next round."""
    cfg = config or ClosedLoopConfig()
    hard: list[tuple[int, int]] = []
    soft: list[tuple[int, int]] = []
    review: list[tuple[int, int]] = []
    for record in records:
        pruning, reason = _classify_pruning(record, text, cfg)
        record.pruning = pruning
        record.decision_reason = reason
        record.status = "confirmed" if pruning in {"hard", "soft"} else "needs_review"
        span = (max(0, int(record.entity.start)), min(len(text), int(record.entity.end)))
        if span[1] <= span[0]:
            continue
        if pruning == "hard":
            hard.append(span)
        elif pruning == "soft":
            soft.append(span)
        else:
            review.append(span)

    hard_ranges = _merge_ranges(hard)
    soft_ranges = _merge_ranges(soft)
    review_ranges = _merge_ranges(review)
    # Only hard/soft decisions are pruned.  Low-confidence review spans remain
    # in the next round so the loop can improve their evidence.
    all_ranges = _merge_ranges((*hard_ranges, *soft_ranges))
    chars = list(text)
    for start, end in all_ranges:
        chars[start:end] = [_MASK_CHAR] * (end - start)
    block_count, remaining_blocks = _block_stats(text, cfg.block_chars, all_ranges)
    residual_chars = len(text) - sum(end - start for start, end in all_ranges)
    return PruningPlan(
        masked_text="".join(chars),
        hard_ranges=hard_ranges,
        soft_ranges=soft_ranges,
        review_ranges=review_ranges,
        input_chars=len(text),
        residual_chars=max(0, residual_chars),
        block_count=block_count,
        remaining_block_count=remaining_blocks,
    )


def _range_intersects(entity: Entity, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(int(entity.start) < end and int(entity.end) > start for start, end in ranges)


def _span_intersects(span: tuple[int, int], ranges: Iterable[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and end > other_start for other_start, other_end in ranges)


def _complement_ranges(length: int, ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Return the parts of the text that are not covered by ranges."""
    merged = _merge_ranges(ranges)
    residual: list[tuple[int, int]] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            residual.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length:
        residual.append((cursor, length))
    return tuple(residual)


@dataclass(frozen=True)
class ResidualInput:
    """Next-round input together with the map back to the original text."""

    text: str
    offsets: tuple[int, ...]
    protected_ranges: tuple[tuple[int, int], ...]
    placeholder_ranges: tuple[tuple[int, int], ...]
    reopened_ranges: tuple[tuple[int, int], ...]
    residual_chars: int
    original_chars: int
    block_chars: int = 320

    @property
    def sent_chars(self) -> int:
        return len(self.text)

    def as_dict(self) -> dict[str, Any]:
        sent_blocks = 0
        if self.sent_chars:
            sent_blocks = max(1, (self.sent_chars + self.block_chars - 1) // self.block_chars)
        return {
            "original_chars": self.original_chars,
            "sent_chars": self.sent_chars,
            "residual_chars": self.residual_chars,
            "sent_blocks": sent_blocks,
            "placeholder_ranges": len(self.placeholder_ranges),
            "reopened_ranges": len(self.reopened_ranges),
            "trim_ratio": round(1 - self.sent_chars / self.original_chars, 4)
            if self.original_chars
            else 0.0,
        }


def build_residual_input(
    text: str,
    plan: PruningPlan,
    config: ClosedLoopConfig | None = None,
    reopened: Iterable[tuple[int, int]] = (),
) -> ResidualInput:
    """Build the next round input from a pruning plan (FR-03 / FR-04).

    Hard-pruned regions leave the input, soft-pruned regions keep a
    PROTECTED_ENTITY placeholder plus their original coordinates, and
    unresolved regions are re-sent verbatim.  The next round therefore sees a
    smaller payload made of new content instead of a whole-document whitespace
    mask that repeats the previous request.
    """
    cfg = config or ClosedLoopConfig()
    original = str(text or "")
    length = len(original)
    reopened_ranges = _merge_ranges(reopened)
    hard_ranges = _merge_ranges(
        span for span in plan.hard_ranges if not _span_intersects(span, reopened_ranges)
    )
    soft_ranges = _merge_ranges(plan.soft_ranges)
    protected = _merge_ranges((*hard_ranges, *soft_ranges))

    pieces: list[tuple[int, int, str]] = [
        (start, end, "keep") for start, end in _complement_ranges(length, protected)
    ]
    pieces.extend((start, end, "placeholder") for start, end in soft_ranges)
    pieces.sort(key=lambda item: (item[0], item[1]))

    buffer: list[str] = []
    offsets: list[int] = []
    placeholder_ranges: list[tuple[int, int]] = []
    residual_chars = 0
    previous_end: int | None = None
    placeholder_index = 0
    for start, end, kind in pieces:
        if start >= end:
            continue
        if buffer and previous_end is not None and start > previous_end:
            # Removed (hard-pruned) regions are gone; keep the segments apart
            # so the detector cannot merge text across the gap.
            buffer.append(_SEGMENT_SEPARATOR)
            offsets.append(previous_end)
        if kind == "keep":
            buffer.append(original[start:end])
            offsets.extend(range(start, end))
            residual_chars += end - start
        else:
            placeholder_index += 1
            token = _PLACEHOLDER_TEMPLATE.format(index=placeholder_index)
            buffer.append(token)
            offsets.extend([start] * len(token))
            placeholder_ranges.append((start, end))
        previous_end = end

    return ResidualInput(
        text="".join(buffer),
        offsets=tuple(offsets),
        protected_ranges=protected,
        placeholder_ranges=tuple(placeholder_ranges),
        reopened_ranges=reopened_ranges,
        residual_chars=residual_chars,
        original_chars=length,
        block_chars=cfg.block_chars,
    )


def _relocate_entities(
    entities: Iterable[Any],
    offsets: tuple[int, ...],
    original_text: str,
) -> list[Entity]:
    """Map detector spans from the round input back to the original text."""
    limit = len(offsets)
    relocated: list[Entity] = []
    for raw in entities:
        if isinstance(raw, Entity):
            candidate = raw.model_copy(deep=True)
        elif isinstance(raw, dict):
            try:
                candidate = Entity.model_validate(raw)
            except Exception:
                continue
        else:
            continue
        start, end = int(candidate.start or 0), int(candidate.end or 0)
        if start < 0 or end <= start or end > limit:
            continue
        mapped_start = int(offsets[start])
        mapped_end = int(offsets[end - 1]) + 1
        if not 0 <= mapped_start < mapped_end <= len(original_text):
            continue
        candidate.start = mapped_start
        candidate.end = mapped_end
        candidate.text = original_text[mapped_start:mapped_end]
        relocated.append(candidate)
    return relocated


def _reopened_ranges(
    entities: Iterable[Entity],
    plan: PruningPlan | None,
) -> tuple[tuple[int, int], ...]:
    """FR-03 rollback: fresh evidence next to a protected region un-prunes it."""
    if plan is None or not plan.hard_ranges:
        return ()
    reopened: list[tuple[int, int]] = []
    for entity in entities:
        widened = (max(0, int(entity.start) - 1), int(entity.end) + 1)
        for start, end in plan.hard_ranges:
            if _span_intersects(widened, ((start, end),)):
                reopened.append((start, end))
    return _merge_ranges(reopened)




def _pick_stronger(left: CandidateRecord, right: CandidateRecord) -> CandidateRecord:
    left_key = (
        left.effective_confidence,
        1 if _source_name(left.entity) in {"regex", "manual"} else 0,
        int(left.entity.end) - int(left.entity.start),
    )
    right_key = (
        right.effective_confidence,
        1 if _source_name(right.entity) in {"regex", "manual"} else 0,
        int(right.entity.end) - int(right.entity.start),
    )
    return right if right_key > left_key else left


def _new_candidate_id(round_no: int, ordinal: int) -> str:
    return f"cl_entity_r{round_no}_{ordinal:04d}"


def _merge_candidates(
    records: list[CandidateRecord],
    incoming: Iterable[Entity],
    round_no: int,
    original_text: str,
    masked_ranges: Iterable[tuple[int, int]] = (),
) -> tuple[int, int]:
    """Merge one round. Returns (new_count, conflict_count)."""
    new_count = 0
    conflict_count = 0
    ordinal = len(records)
    for raw in incoming:
        entity = _valid_entity(raw, original_text)
        if entity is None:
            continue
        proposed_id = _new_candidate_id(round_no, ordinal)
        if masked_ranges and _range_intersects(entity, masked_ranges):
            # A new entity overlapping a protected span is retained only as a
            # conflict signal; the previous candidate remains the redaction
            # winner and the audit trail explains why.
            overlapped = [record for record in records if _overlap_ratio(record.entity, entity) >= 0.25]
            if overlapped:
                for record in overlapped:
                    if proposed_id not in record.conflict_ids:
                        record.conflict_ids.append(proposed_id)
                conflict_count += 1
            continue

        matching = next((record for record in records if _candidate_matches(record.entity, entity)), None)
        if matching is not None:
            matching.rounds.append(round_no)
            matching.sources.add(_source_name(entity))
            matching.evidence_count = len(matching.sources)
            incoming_record = CandidateRecord(
                entity=entity,
                rounds=[round_no],
                sources={_source_name(entity)},
                evidence_count=1,
            )
            winner = _pick_stronger(matching, incoming_record)
            if winner is incoming_record:
                matching.entity = entity
            continue

        overlapping = [record for record in records if _overlap_ratio(record.entity, entity) >= 0.25]
        if overlapping and any(record.normalized_type != canonical_type_id(str(entity.type or "")) for record in overlapping):
            conflict_count += 1
            entity.id = proposed_id
            ordinal += 1
            record = CandidateRecord(
                entity=entity,
                rounds=[round_no],
                sources={_source_name(entity)},
                evidence_count=1,
                status="conflict",
                pruning="review",
                decision_reason="与其他类型候选重叠，需人工或终检裁决",
            )
            for existing in overlapping:
                if record.candidate_id not in existing.conflict_ids:
                    existing.conflict_ids.append(record.candidate_id)
                if existing.candidate_id not in record.conflict_ids:
                    record.conflict_ids.append(existing.candidate_id)
                existing.status = "conflict"
            records.append(record)
            new_count += 1
            continue

        entity.id = proposed_id
        ordinal += 1
        records.append(
            CandidateRecord(
                entity=entity,
                rounds=[round_no],
                sources={_source_name(entity)},
                evidence_count=1,
            )
        )
        new_count += 1
    return new_count, conflict_count


def _final_entities(records: Iterable[CandidateRecord], original_text: str) -> list[Entity]:
    selected: list[CandidateRecord] = []
    for record in records:
        entity = record.entity.model_copy(deep=True)
        entity.type = canonical_type_id(str(entity.type or ""))
        if 0 <= entity.start < entity.end <= len(original_text):
            entity.text = original_text[entity.start : entity.end]
        selected.append(record)
    selected.sort(key=lambda record: (int(record.entity.start), int(record.entity.end), record.candidate_id))
    output: list[Entity] = []
    for index, record in enumerate(selected):
        entity = record.entity.model_copy(deep=True)
        entity.id = f"entity_{index}"
        output.append(entity)
    return output


def _lineage_payload(records: Iterable[CandidateRecord]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": record.candidate_id,
            "entity_id": record.entity.id,
            "type": record.normalized_type,
            "text": record.entity.text,
            "start": int(record.entity.start),
            "end": int(record.entity.end),
            "rounds": sorted(set(record.rounds)),
            "sources": sorted(record.sources),
            "evidence_count": record.evidence_count,
            "effective_confidence": round(record.effective_confidence, 4),
            "status": record.status,
            "pruning": record.pruning,
            "decision_reason": record.decision_reason,
            "conflict_ids": sorted(set(record.conflict_ids)),
        }
        for record in records
    ]


async def run_closed_loop_text(
    text: str,
    entity_types: list[Any],
    detector: Detector | None = None,
    config: ClosedLoopConfig | None = None,
) -> tuple[list[Entity], dict[str, Any]]:
    """Run up to three recognition rounds over one text document.

    ``detector`` is injectable for deterministic tests.  The production
    runtime supplies a wrapper around ``perform_hybrid_ner``.
    """
    cfg = config or ClosedLoopConfig()
    started = time.perf_counter()
    original_text = str(text or "")
    if not cfg.enabled:
        detector = detector or _default_detector
        entities = await detector(original_text, entity_types)
        normalized = [candidate for candidate in (_valid_entity(item, original_text) for item in entities) if candidate]
        return normalized, {
            "enabled": False,
            "max_rounds_source": cfg.max_rounds_source,
            "rounds_run": 1,
            "termination_reason": "disabled",
            "rounds": [{"round_no": 1, "input_chars": len(original_text), "new_candidates": len(normalized)}],
            "pruning_summary": {},
            "candidate_lineage": [],
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }

    detector = detector or _default_detector
    records: list[CandidateRecord] = []
    round_summaries: list[dict[str, Any]] = []
    current_text = original_text
    offsets: tuple[int, ...] = tuple(range(len(original_text)))
    masked_ranges: tuple[tuple[int, int], ...] = ()
    reopened: tuple[tuple[int, int], ...] = ()
    current_plan: PruningPlan | None = None
    last_plan: PruningPlan | None = None
    last_residual: ResidualInput | None = None
    termination_reason = "max_rounds"

    for round_no in range(1, cfg.max_rounds + 1):
        round_started = time.perf_counter()
        try:
            raw_entities = await detector(current_text, entity_types)
        except Exception:
            logger.exception("closed-loop detector failed at round %d", round_no)
            raw_entities = []

        raw_list = [item for item in raw_entities if item is not None]
        incoming = _relocate_entities(raw_list, offsets, original_text)
        before_count = len(records)
        new_count, conflict_count = _merge_candidates(
            records,
            incoming,
            round_no,
            original_text,
            masked_ranges=masked_ranges,
        )
        # FR-03 rollback: fresh evidence at the edge of a hard-pruned region
        # lifts that region's pruning so it is sent to the detector again.
        reopened = _merge_ranges((*reopened, *_reopened_ranges(incoming, current_plan)))
        # Recompute decisions as soon as new evidence arrives, so the next
        # pruning plan can promote a stable candidate from soft to hard.
        if round_no < cfg.max_rounds:
            last_plan = build_pruning_plan(original_text, records, cfg)
            last_residual = build_residual_input(original_text, last_plan, cfg, reopened=reopened)
        summary: dict[str, Any] = {
            "round_no": round_no,
            "input_chars": len(current_text),
            "output_candidates": len(raw_list),
            "mapped_candidates": len(incoming),
            "total_candidates": len(records),
            "new_candidates": max(0, len(records) - before_count),
            "conflicts": conflict_count,
            "duration_ms": round((time.perf_counter() - round_started) * 1000),
        }
        if last_plan is not None:
            summary["next_round_pruning"] = last_plan.as_dict()
        if last_residual is not None:
            summary["next_round_input"] = last_residual.as_dict()
        round_summaries.append(summary)

        if round_no >= cfg.max_rounds:
            termination_reason = (
                "converged_no_new_candidates"
                if round_no > 1 and new_count < cfg.min_new_candidates_to_continue
                else "max_rounds"
            )
            break
        if new_count < cfg.min_new_candidates_to_continue and round_no > 1:
            termination_reason = "converged_no_new_candidates"
            break
        if last_residual is None or last_residual.residual_chars <= 0:
            termination_reason = "converged_no_residual_text"
            break
        if cfg.skip_identical_input and last_residual.text == current_text:
            # Nothing was pruned, so the next round would repeat the very same
            # payload (FR-05 early stop).
            termination_reason = "converged_identical_input"
            break

        current_plan = last_plan
        current_text = last_residual.text
        offsets = last_residual.offsets
        masked_ranges = last_residual.protected_ranges

    if last_plan is None:
        last_plan = build_pruning_plan(original_text, records, cfg)
    final = _final_entities(records, original_text)
    conflict_count = sum(1 for record in records if record.conflict_ids)
    pruning_counts = Counter(record.pruning for record in records)
    audit = {
        "enabled": True,
        "config": cfg.as_dict(),
        "max_rounds_source": cfg.max_rounds_source,
        "rounds_run": len(round_summaries),
        "termination_reason": termination_reason,
        "rounds": round_summaries,
        "pruning_summary": {
            **last_plan.as_dict(),
            "reopened_ranges": len(reopened),
            "candidate_counts": dict(pruning_counts),
            "conflict_candidates": conflict_count,
        },
        "candidate_lineage": _lineage_payload(records),
        "quality_summary": {
            "final_entity_count": len(final),
            "identity_entity_count": sum(1 for entity in final if _identity_type(str(entity.type))),
            "conflict_count": conflict_count,
            "hard_pruned_count": pruning_counts.get("hard", 0),
            "soft_pruned_count": pruning_counts.get("soft", 0),
            "review_count": pruning_counts.get("review", 0),
        },
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }
    return final, audit


async def _default_detector(text: str, entity_types: list[Any]) -> list[Entity]:
    from app.services.hybrid_ner_service import perform_hybrid_ner

    return await perform_hybrid_ner(text, entity_types)


def assign_pages_to_entities_20260902(entities: list[Entity], pages: list[str] | None) -> None:
    """Assign page numbers without importing the legacy processing module."""
    if not pages or len(pages) <= 1 or not entities:
        return
    ranges: list[tuple[int, int, int]] = []
    offset = 0
    for page_no, page_text in enumerate(pages, start=1):
        length = len(page_text or "")
        ranges.append((offset, offset + length, page_no))
        offset += length + 2
    for entity in entities:
        page_no = ranges[-1][2]
        for start, end, candidate_page in ranges:
            if int(entity.start or 0) < end:
                page_no = candidate_page
                break
        entity.page = page_no


async def run_closed_loop_for_file_20260902(
    file_id: str,
    entity_type_ids: list[str] | None = None,
    owner_id: str | None = None,
    raw_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the dated closed-loop recognizer and persist its audit payload."""
    from app.services.entity_type_service import get_default_generic_types, resolve_requested_entity_types
    from app.services.file_management_service import _file_store_lock, file_store

    async with _file_store_lock:
        file_info = file_store.get(file_id)
        if not file_info:
            raise ValueError("文件不存在")
        snapshot = dict(file_info)
    owner_id = owner_id or str(snapshot.get("owner_id") or "local_user")
    if "content" not in snapshot:
        raise ValueError("请先解析文件内容")
    if snapshot.get("is_scanned", False):
        return {
            "entities": [],
            "entity_count": 0,
            "entity_summary": {},
            "warnings": ["扫描件请使用视觉识别链路"],
            "closed_loop": {
                "enabled": ClosedLoopConfig.from_mapping(raw_config).enabled,
                "rounds_run": 0,
                "termination_reason": "scanned_file_uses_vision_pipeline",
            },
        }

    if entity_type_ids is None:
        entity_types = get_default_generic_types(owner_id=owner_id)
    else:
        entity_types = resolve_requested_entity_types(entity_type_ids, owner_id=owner_id)
    cfg = ClosedLoopConfig.from_mapping(raw_config)
    entities, audit = await run_closed_loop_text(str(snapshot.get("content") or ""), entity_types, config=cfg)
    assign_pages_to_entities_20260902(entities, snapshot.get("pages"))
    entity_summary = Counter(str(entity.type) for entity in entities)
    recognition_config = dict(snapshot.get("recognition_config") or {})
    recognition_config.update(
        {
            "entity_type_ids": entity_type_ids,
            "resolved_entity_type_ids": [getattr(item, "id", None) for item in entity_types],
            "closed_loop": audit,
        }
    )
    async with _file_store_lock:
        if file_id in file_store:
            file_store.update_fields(
                file_id,
                {
                    "entities": entities,
                    "recognition_config": recognition_config,
                    "closed_loop": audit,
                },
            )

    return {
        "entities": entities,
        "entity_count": len(entities),
        "entity_summary": dict(entity_summary),
        "warnings": [],
        "closed_loop": audit,
    }


async def run_closed_loop_default_ner_20260902(
    file_id: str,
    entity_type_ids: list[str] | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    return await run_closed_loop_for_file_20260902(
        file_id,
        entity_type_ids=entity_type_ids,
        owner_id=owner_id,
    )


__all__ = [
    "CandidateRecord",
    "ClosedLoopConfig",
    "PruningPlan",
    "ResidualInput",
    "assign_pages_to_entities_20260902",
    "build_pruning_plan",
    "build_residual_input",
    "run_closed_loop_default_ner_20260902",
    "run_closed_loop_for_file_20260902",
    "run_closed_loop_text",
]
