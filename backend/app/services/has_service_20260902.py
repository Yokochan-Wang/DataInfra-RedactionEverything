"""Versioned HaS NER batching overlay for the 2026-09-02 runtime.

The legacy :mod:`app.services.has_service` module is intentionally left
unchanged.  The production singleton is patched at runtime so each semantic
recognition round sends the complete selected entity-type list to the remote
Qwen endpoint in one request instead of creating one request per type.
"""
from __future__ import annotations

import logging
from types import MethodType
from typing import Any

logger = logging.getLogger(__name__)

_PATCH_MARKER = "_single_batch_20260902_installed"


def _all_types_in_one_batch(
    service: Any,
    entity_types: list[Any],
) -> list[list[Any]]:
    """Return one deduplicated batch containing every selected type."""
    seen_chinese_types: set[str] = set()
    ordered_types: list[Any] = []

    for entity_type in entity_types:
        # Keep the same user-facing type-name semantics as the legacy service
        # and deduplicate only exact model query names.
        chinese_types = service._convert_entity_types_to_chinese([entity_type])
        if not chinese_types:
            continue
        chinese_type = chinese_types[0]
        if chinese_type in seen_chinese_types:
            continue
        seen_chinese_types.add(chinese_type)
        ordered_types.append(entity_type)

    if not ordered_types:
        return []

    logger.info(
        "HaS NER batched %d requested types into 1 all-types request",
        len(ordered_types),
    )
    return [ordered_types]


def install_single_batch_has_ner_20260902() -> None:
    """Patch only the production HaS singleton with all-types batching.

    The patch is idempotent and scoped to the singleton used by
    ``HybridNERService``.  Legacy modules and independently constructed test
    services retain their original behavior.
    """
    from app.services.has_service import has_service

    if getattr(has_service, _PATCH_MARKER, False):
        return

    has_service._iter_ner_type_batches = MethodType(_all_types_in_one_batch, has_service)
    setattr(has_service, _PATCH_MARKER, True)
    logger.info("HaS NER single all-types batching overlay 20260902 installed")


__all__ = ["install_single_batch_has_ner_20260902"]
