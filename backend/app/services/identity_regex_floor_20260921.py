"""Deterministic identity-number floor for the hybrid NER pipeline.

The closed loop defaults the ``external`` runtime to a single detector round
(measured: only 3 of ~240 golden-corpus entities ever first appeared in round
>= 2).  A single LLM pass can still miss a well-formed identity number, and the
identity-recall gate is 100%.  This module adds a deterministic FLOOR: a small,
ordered registry of high-precision patterns that guarantee recall for
well-formed identity numbers regardless of what the LLM returns.

Design decisions (do not "fix" these without re-reading why):

1. **Union semantics.**  A floor entity is added only where its span does not
   overlap any entity already returned by the pipeline.  LLM typing always
   wins on covered spans; the floor only fills gaps.  It never re-types,
   duplicates, or outranks a pipeline entity.

2. **No checksum / no Luhn gating.**  The legal golden identity entity
   ``6217001234509876543`` FAILS the Luhn check, and the synthetic corpus IDs
   fail ISO-7064; gating on either would break the 100% identity-recall hard
   gate.  There is a non-checksum precedent in ``app.services.structured_profile``
   (``\\d{17}[\\dXx]`` / ``\\d{12,19}``).  False positives are bounded by the
   corpus FP scan (``scripts/eval/scan_regex_floor_fp.py``) and by union
   semantics: a span the LLM already typed is never re-claimed.

   BANK_CARD additionally constrains the leading digit to ``[3-6]`` (ISO/IEC
   7812 major-industry identifier: 3=travel/entertainment, 4=Visa,
   5=Mastercard, 6=UnionPay/discovery — no network issues cards starting with
   1).  Without it the floor claims a lawyer's 17-digit practice-certificate
   number (``执业证号：13301201010223344``) as a bank card; every golden
   bank entity in the corpus starts with 6, so the constraint costs nothing.

3. **CJK-safe lookarounds — never ``\\b``.**  In Python 3 ``\\w`` includes CJK
   ideographs, so ``\\b`` never fires between a digit and an adjacent CJK char
   (``账号6217001234509876543``).  Every digit-run pattern therefore uses
   ``(?<!\\d)`` / ``(?!\\d)`` lookarounds, and the letter-run patterns use
   ``(?<![A-Za-z])`` / ``(?<![A-Za-z0-9])`` boundaries.

4. **Priority order = insertion order of ``FLOOR_PATTERNS``.**  ID_CARD is
   tried before BANK_CARD so an 18-digit ID is never re-claimed as a card, and
   spans claimed by an earlier pattern are skipped by later ones.

Placement: the pipeline calls ``apply_identity_regex_floor`` AFTER Stage 3
cross-validation, so floor entities cannot interfere with dedupe, ranking,
propagation or coref.  Documented fallback: if future tests show floor
entities must participate in coref/propagation, move the call site so the
floor entities join ``all_entities`` before Stage 3's ``_cross_validate``
(e.g. right after Stage 1, before Stage 2).
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from app.models.schemas import Entity
from app.models.type_mapping import canonical_type_id

# Ordered registry: insertion order IS the application priority.
FLOOR_PATTERNS: dict[str, re.Pattern[str]] = {
    "ID_CARD": re.compile(r"(?<!\d)\d{17}[\dXx](?![\dXx])"),
    "BANK_CARD": re.compile(r"(?<!\d)[3-6]\d{15,18}(?!\d)"),
    "PASSPORT": re.compile(r"(?<![A-Za-z])[EGDSP]\d{8}(?!\d)"),
    "PHONE": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "EMAIL": re.compile(
        r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?!\w)"
    ),
    "MED_RECORD_ID": re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{8,12}(?!\d)"),
    "INPATIENT_NO": re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{8,12}(?!\d)"),
}

FLOOR_PAGE = 1
_FLOOR_ID_PREFIX = "regex_floor_"

# BANK_CARD vs BANK_ACCOUNT is a label decision, not a format decision (both
# are 16-19 digit runs).  A BANK_CARD entity preceded by an account label is
# an account; a 卡号/银行卡号 context keeps it a card.
_BANK_ACCOUNT_LABELS = ("账户", "账号", "户名", "帐户")
_BANK_ACCOUNT_LABEL_WINDOW = 4


def apply_identity_regex_floor(
    text: str,
    entity_types: Sequence[Any],
    entities: list[Entity],
) -> list[Entity]:
    """Union deterministic identity-number floor entities into ``entities``.

    Returns a NEW list: ``entities`` followed by the floor entities in
    ``FLOOR_PATTERNS`` priority order.  The input list is not mutated.  A
    pattern fires only when its canonical type id is present in
    ``entity_types`` (matched on the type config ``id``, case-insensitive),
    and only on spans that do not overlap an existing entity or an
    already-claimed floor span.
    """
    requested_type_ids = {
        str(getattr(entity_type, "id", "") or "").strip().lower()
        for entity_type in entity_types
    }
    requested_type_ids.discard("")

    claimed_spans: list[tuple[int, int]] = [
        (entity.start, entity.end) for entity in entities
    ]
    result: list[Entity] = list(entities)
    floor_count = 0

    for type_id, pattern in FLOOR_PATTERNS.items():
        if type_id.lower() not in requested_type_ids:
            continue
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(
                start < claimed_end and claimed_start < end
                for claimed_start, claimed_end in claimed_spans
            ):
                continue
            claimed_spans.append((start, end))
            floor_count += 1
            result.append(
                Entity(
                    id=f"{_FLOOR_ID_PREFIX}{floor_count:04d}",
                    text=match.group(),
                    type=type_id,
                    start=start,
                    end=end,
                    page=FLOOR_PAGE,
                    confidence=None,
                    source="regex",
                    coref_id=None,
                )
            )
    return result


def _has_bank_account_label(prefix: str) -> bool:
    """True when an account label ends the prefix with no 卡 between it and the span."""
    for label in _BANK_ACCOUNT_LABELS:
        index = prefix.rfind(label)
        if index < 0:
            continue
        if "卡" not in prefix[index + len(label):]:
            return True
    return False


def retype_labeled_bank_accounts(
    entities: list[Entity],
    text: str,
    entity_types: Sequence[Any],
) -> list[Entity]:
    """Retype BANK_CARD entities preceded by an account label to BANK_ACCOUNT.

    Applies to LLM- and floor-sourced entities alike and only fires when
    BANK_ACCOUNT is a requested type (otherwise the retyped label would not
    be in the caller's recognition list).  A 卡号/银行卡号 context between the
    label and the span keeps the BANK_CARD typing.
    """
    requested_type_ids = {
        str(getattr(entity_type, "id", "") or "").strip().lower()
        for entity_type in entity_types
    }
    if "bank_account" not in requested_type_ids:
        return list(entities)

    result: list[Entity] = []
    for entity in entities:
        if canonical_type_id(str(entity.type or "")) == "BANK_CARD":
            prefix = text[max(0, int(entity.start) - _BANK_ACCOUNT_LABEL_WINDOW):int(entity.start)]
            if _has_bank_account_label(prefix):
                retyped = entity.model_copy(deep=True)
                retyped.type = "BANK_ACCOUNT"
                result.append(retyped)
                continue
        result.append(entity)
    return result


__all__ = [
    "FLOOR_PATTERNS",
    "apply_identity_regex_floor",
    "retype_labeled_bank_accounts",
]
