"""Closed-loop recognition API (versioned additive surface)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi import Query
from pydantic import BaseModel, Field

from app.core.auth import require_auth
from app.models.schemas import VisionDetectRequest
from app.services import file_management_service, redaction_orchestrator
from app.services.closed_loop_recognition_20260902 import run_closed_loop_for_file_20260902

router = APIRouter()


class ClosedLoopNerRequest20260902(BaseModel):
    entity_type_ids: list[str] | None = Field(
        default=None,
        description="实体类型 ID；None 使用默认启用项，[] 表示不识别文本类型",
    )
    closed_loop: dict[str, Any] = Field(
        default_factory=dict,
        description="闭环配置，如 max_rounds、剪枝阈值和 identity_final_check",
    )


@router.post("/files/{file_id}/ner/closed-loop")
async def run_closed_loop_ner_20260902(
    file_id: str,
    request: ClosedLoopNerRequest20260902 = Body(default=ClosedLoopNerRequest20260902()),
    owner_id: str = Depends(require_auth),
):
    """显式运行三轮闭环文本识别并返回跨轮审计信息。"""
    if request.entity_type_ids is not None and len(request.entity_type_ids) > 200:
        raise HTTPException(status_code=400, detail="实体类型数量超过上限（200）")
    try:
        file_management_service.assert_file_owner(file_id, owner_id)
        result = await run_closed_loop_for_file_20260902(
            file_id,
            entity_type_ids=request.entity_type_ids,
            owner_id=owner_id,
            raw_config={"closed_loop": request.closed_loop},
        )
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(status_code=404 if "不存在" in detail else 400, detail=detail) from exc
    return result


@router.get("/files/{file_id}/ner/closed-loop")
async def get_closed_loop_ner_20260902(file_id: str, owner_id: str = Depends(require_auth)):
    """读取最近一次闭环识别的轮次、剪枝和候选血缘。"""
    info = await file_management_service.get_file_info(file_id)
    if not info or file_management_service.file_owner_id(info) != owner_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    audit = info.get("closed_loop") or (info.get("recognition_config") or {}).get("closed_loop") or {}
    return {
        "file_id": file_id,
        "entity_count": len(info.get("entities") or []),
        "closed_loop": audit,
    }


@router.post("/files/{file_id}/ner/hybrid-20260902")
async def run_configurable_hybrid_ner_20260902(
    file_id: str,
    request: ClosedLoopNerRequest20260902 = Body(default=ClosedLoopNerRequest20260902()),
    owner_id: str = Depends(require_auth),
):
    """Run one-pass or three-round text recognition from the UI toggle."""
    if request.entity_type_ids is not None and len(request.entity_type_ids) > 200:
        raise HTTPException(status_code=400, detail="实体类型数量超过上限（200）")
    try:
        file_management_service.assert_file_owner(file_id, owner_id)
        return await run_closed_loop_for_file_20260902(
            file_id,
            entity_type_ids=request.entity_type_ids,
            owner_id=owner_id,
            raw_config={"closed_loop": request.closed_loop},
        )
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(status_code=404 if "不存在" in detail else 400, detail=detail) from exc


@router.post("/redaction/{file_id}/vision-20260902")
async def run_configurable_vision_20260902(
    file_id: str,
    page: int = 1,
    force: bool = False,
    include_result_image: bool = True,
    closed_loop: bool = Query(True, description="是否执行闭环复检"),
    request: VisionDetectRequest | None = None,
    owner_id: str = Depends(require_auth),
):
    """Run visual recognition with the closed-loop/one-pass UI choice."""
    try:
        file_management_service.assert_file_owner(file_id, owner_id)
        return await redaction_orchestrator.detect_vision(
            file_id=file_id,
            page=page,
            selected_ocr_has_types=request.selected_ocr_has_types if request else None,
            selected_visual_feature_types=request.selected_visual_feature_types if request else None,
            has_request=request is not None,
            force=force,
            include_result_image=include_result_image,
            owner_id=owner_id,
            closed_loop_enabled=closed_loop,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
