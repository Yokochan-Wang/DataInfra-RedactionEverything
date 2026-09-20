"""Runtime overlay for the dated closed-loop recognizer.

The original application modules are intentionally left untouched.  Importing
``app.main_20260902`` installs these wrappers before FastAPI starts its
lifespan, so both direct NER requests and queued text jobs use the new
recognizer.  The overlay is idempotent and can safely be imported by tests.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_INSTALLED = False


def _env_bool_runtime(name: str, default: bool) -> bool:
    import os

    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _closed_loop_enabled(config: dict[str, Any] | None = None) -> bool:
    from app.services.closed_loop_recognition_20260902 import ClosedLoopConfig

    return ClosedLoopConfig.from_mapping(config).enabled


def _vision_page_needs_recheck_20260902(result: Any, *, visual_requested: bool, soft_threshold: float) -> bool:
    """Return whether a page has unresolved visual evidence.

    Pages with stable boxes and no pipeline warnings are treated as consumed
    and skipped by later rounds.  Empty pages are rechecked only when the
    visual pipeline actually ran; OCR-only stages do not get duplicate calls.
    """
    warnings = list(getattr(result, "warnings", []) or [])
    if any("blank" in str(item).lower() or "skip" in str(item).lower() for item in warnings):
        return False
    status = getattr(result, "pipeline_status", {}) or {}
    failed = any(
        isinstance(value, dict) and value.get("failed")
        for value in status.values()
    )
    if failed:
        return True
    boxes = list(getattr(result, "bounding_boxes", []) or [])
    if not boxes:
        return bool(visual_requested and any(isinstance(value, dict) and value.get("ran") for value in status.values()))
    return any(
        getattr(box, "confidence", None) is not None
        and float(getattr(box, "confidence")) < soft_threshold
        for box in boxes
    )


def install_closed_loop_runtime_20260902() -> None:
    """Install versioned wrappers without modifying legacy module files."""
    global _INSTALLED
    if _INSTALLED:
        return

    from app.services.has_service_20260902 import install_single_batch_has_ner_20260902

    # Send every selected semantic type to Qwen in one request per round.
    # This is installed before any recognition wrapper is invoked and leaves
    # the legacy HaS service module untouched.
    install_single_batch_has_ner_20260902()

    from app.services import file_management_service, file_processing_service
    from app.services.closed_loop_recognition_20260902 import (
        ClosedLoopConfig,
        run_closed_loop_for_file_20260902,
    )

    legacy_hybrid = file_processing_service.run_hybrid_ner
    legacy_default = file_processing_service.run_default_ner

    async def run_hybrid_ner_20260902(
        file_id: str,
        entity_type_ids: list[str] | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        cfg = ClosedLoopConfig.from_mapping()
        if not cfg.enabled:
            return await legacy_hybrid(file_id, entity_type_ids=entity_type_ids, owner_id=owner_id)
        return await run_closed_loop_for_file_20260902(
            file_id,
            entity_type_ids=entity_type_ids,
            owner_id=owner_id,
        )

    async def run_default_ner_20260902(
        file_id: str,
        entity_type_ids: list[str] | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        cfg = ClosedLoopConfig.from_mapping()
        if not cfg.enabled:
            return await legacy_default(file_id, entity_type_ids=entity_type_ids, owner_id=owner_id)
        return await run_closed_loop_for_file_20260902(
            file_id,
            entity_type_ids=entity_type_ids,
            owner_id=owner_id,
        )

    # The service module re-exports these functions at import time, so patch
    # both module attributes.  Existing callers resolve the wrappers without
    # changing their import statements.
    file_processing_service.run_hybrid_ner = run_hybrid_ner_20260902
    file_processing_service.run_default_ner = run_default_ner_20260902
    file_management_service.run_hybrid_ner = run_hybrid_ner_20260902
    file_management_service.run_default_ner = run_default_ner_20260902

    # Page-level visual closed loop.  The existing detector remains the source
    # of truth; this wrapper only repeats unresolved pages and asks it to merge
    # new evidence with the first-round boxes.  Stable pages are hard-pruned at
    # page granularity, which is the safe acceleration available to the
    # current visual API (region-level masks are not yet accepted by the model
    # service).
    from app.services import redaction_orchestrator
    from app.services.closed_loop_recognition_20260902 import ClosedLoopConfig

    legacy_detect_vision = redaction_orchestrator.detect_vision

    async def detect_vision_20260902(*args: Any, **kwargs: Any) -> Any:
        cfg = ClosedLoopConfig.from_mapping()
        requested_closed_loop = kwargs.pop("closed_loop_enabled", None)
        started = time.perf_counter()
        first = await legacy_detect_vision(*args, **kwargs)
        # The dated one-pass endpoint uses the same detector and persistence
        # path, but deliberately skips retries for large/latency-sensitive
        # files.  It still returns the legacy stage timing.
        if requested_closed_loop is False:
            return first
        if not cfg.enabled or cfg.max_rounds <= 1:
            return first
        # Explicitly empty visual types means this invocation is the OCR-only
        # phase of the multi-page scheduler; only retry on an actual failure.
        selected_visual = kwargs.get("selected_visual_feature_types")
        visual_requested = selected_visual != []
        if not _closed_loop_enabled() or not _env_bool_runtime("CLOSED_LOOP_VISION_RECHECK", True):
            return first

        results = [first]
        for round_no in range(2, cfg.max_rounds + 1):
            current = results[-1]
            if not _vision_page_needs_recheck_20260902(
                current,
                visual_requested=visual_requested,
                soft_threshold=cfg.soft_prune_confidence,
            ):
                break
            retry_kwargs = dict(kwargs)
            retry_kwargs.update({"force": True, "merge_existing": True, "include_result_image": False})
            retry = await legacy_detect_vision(*args, **retry_kwargs)
            results.append(retry)
            # A successful retry with no new boxes is a convergence signal.
            previous_count = len(getattr(current, "bounding_boxes", []) or [])
            current_count = len(getattr(retry, "bounding_boxes", []) or [])
            if current_count <= previous_count and not _vision_page_needs_recheck_20260902(
                retry,
                visual_requested=visual_requested,
                soft_threshold=cfg.soft_prune_confidence,
            ):
                break

        last = results[-1]
        closed_loop_rounds = len(results)
        duration_ms = dict(getattr(last, "duration_ms", {}) or {})
        duration_ms["closed_loop_total_ms"] = round((time.perf_counter() - started) * 1000)
        duration_ms["closed_loop_rounds"] = closed_loop_rounds
        try:
            last.duration_ms = duration_ms
            last.cache_status = {
                **dict(getattr(last, "cache_status", {}) or {}),
                "closed_loop": True,
                "rounds": closed_loop_rounds,
            }
        except Exception:
            logger.debug("unable to annotate closed-loop vision result", exc_info=True)

        file_id = kwargs.get("file_id") or (args[0] if args else None)
        page = kwargs.get("page") or (args[1] if len(args) > 1 else 1)
        if file_id:
            try:
                lock = file_management_service._file_store_lock
                async with lock:
                    info = file_management_service.file_store.get(str(file_id))
                    if info is not None:
                        audit = dict(info.get("closed_loop") or {})
                        pages = dict(audit.get("vision_pages") or {})
                        pages[str(page)] = {
                            "mode": "vision",
                            "page": int(page),
                            "rounds_run": closed_loop_rounds,
                            "termination_reason": (
                                "converged_stable_page"
                                if closed_loop_rounds < cfg.max_rounds
                                else "max_rounds"
                            ),
                            "first_round_box_count": len(getattr(first, "bounding_boxes", []) or []),
                            "final_box_count": len(getattr(last, "bounding_boxes", []) or []),
                            "warnings": list(getattr(last, "warnings", []) or []),
                        }
                        audit.update(
                            {
                                "enabled": True,
                                "config": cfg.as_dict(),
                                "vision_pages": pages,
                            }
                        )
                        info["closed_loop"] = audit
                        file_management_service.file_store.set(str(file_id), info)
            except Exception:
                logger.debug("unable to persist closed-loop vision audit", exc_info=True)
        return last

    redaction_orchestrator.detect_vision = detect_vision_20260902

    # Batch text jobs call the mixin method directly.  Vision jobs continue to
    # use the legacy page scheduler for now; its persisted page-level quality
    # data remains compatible with the closed-loop audit payload.
    from app.services import task_queue_pipelines

    legacy_run_ner_or_vision = task_queue_pipelines.RecognitionPipelineMixin._run_ner_or_vision

    async def _run_ner_or_vision_20260902(self, task: Any, cfg: dict[str, Any]) -> None:
        from app.services.file_operations import get_file_info

        file_info = get_file_info(task.file_id) or {}
        file_type = str(file_info.get("file_type", ""))
        is_image = file_type == "image" or bool(file_info.get("is_scanned"))
        if is_image or not _closed_loop_enabled(cfg):
            await legacy_run_ner_or_vision(self, task, cfg)
            return

        store = self._get_store()
        entity_type_ids = list(cfg.get("entity_type_ids") or [])
        store.update_item_progress(
            task.item_id,
            stage="ner",
            current=0,
            total=1,
            message="closed_loop_recognition_running",
        )
        owner_id = str(file_info.get("owner_id") or "local_user")
        result = await run_closed_loop_for_file_20260902(
            task.file_id,
            entity_type_ids=entity_type_ids,
            owner_id=owner_id,
            raw_config=cfg,
        )
        audit = result.get("closed_loop") if isinstance(result, dict) else {}
        self._record_item_performance(
            store,
            task.item_id,
            {
                "recognition": {
                    "mode": "closed_loop_text",
                    "closed_loop": audit,
                    "ner_ms": int((audit or {}).get("duration_ms") or 0),
                }
            },
        )
        store.update_item_progress(
            task.item_id,
            stage="ner",
            current=1,
            total=1,
            message="closed_loop_recognition_complete",
        )

    task_queue_pipelines.RecognitionPipelineMixin._run_ner_or_vision = _run_ner_or_vision_20260902
    _INSTALLED = True
    logger.info("Closed-loop recognition runtime overlay 20260902 installed")


__all__ = ["install_closed_loop_runtime_20260902"]
