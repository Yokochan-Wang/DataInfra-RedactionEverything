"""
文本 NER 后端（本地 / 远端模型）运行时配置 API

设置页把本地、远端两份配置写进 data/ner_backend.json，active 决定推理链路
实际用哪一份。优先级高于 .env，保存即生效，无需重启也无需改 .env。
"""
from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.core.llamacpp_probe import probe_llamacpp
from app.core.ner_runtime import (
    DEFAULT_LOCAL_BASE_URL,
    REDACTED_API_KEY,
    NerRuntimeState,
    TextModelProfile,
    delete_ner_runtime,
    load_ner_runtime,
    load_ner_runtime_for_ui,
    save_ner_runtime,
)

router = APIRouter(prefix="/ner-backend", tags=["文本NER后端"])
logger = logging.getLogger(__name__)

_REMOTE_ENV_RUNTIMES = frozenset({"external", "remote"})


def _validate_base_url(base_url: str) -> None:
    """SSRF guard for the NER backend URL (defense-in-depth behind super_admin).

    Requires an http(s) URL with no embedded credentials. When
    NER_BACKEND_HOST_ALLOWLIST is set, the host must match an exact hostname
    or IP/CIDR entry — same shape as the structured-DB host allowlist. None
    (default) keeps the local/intranet self-hosted NER use case working.
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=422, detail="NER 后端地址必须是 http(s):// URL")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="NER 后端地址不得包含账号密码")
    allowlist = get_settings().NER_BACKEND_HOST_ALLOWLIST
    if allowlist is None:
        return
    host = parsed.hostname
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    for raw_entry in allowlist:
        entry = str(raw_entry).strip()
        if not entry:
            continue
        if host == entry:
            return
        if addr is not None:
            try:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return
            except ValueError:
                continue
    raise HTTPException(
        status_code=422,
        detail=f"NER 后端主机 '{host}' 不在 NER_BACKEND_HOST_ALLOWLIST 白名单内",
    )


def _with_hint(msg: str, hint: str | None) -> str:
    return f"{msg} {hint}" if hint else msg


def _mask_profile(profile: TextModelProfile) -> TextModelProfile:
    """读接口不回传明文 Key，只回传哨兵值；写接口收到哨兵值即保留服务端原值。"""
    if not profile.api_key.strip():
        return profile
    return profile.model_copy(update={"api_key": REDACTED_API_KEY})


def _mask_state(state: NerRuntimeState) -> NerRuntimeState:
    return state.model_copy(
        update={"local": _mask_profile(state.local), "remote": _mask_profile(state.remote)}
    )


def _merge_profile(incoming: TextModelProfile, stored: TextModelProfile) -> TextModelProfile:
    api_key = stored.api_key if incoming.api_key == REDACTED_API_KEY else incoming.api_key
    return TextModelProfile(
        base_url=incoming.base_url.strip() or stored.base_url.strip(),
        api_key=api_key,
        model_name=incoming.model_name.strip(),
        display_name=incoming.display_name.strip(),
    )


def _merge_state(body: NerRuntimeState, stored: NerRuntimeState) -> NerRuntimeState:
    return NerRuntimeState(
        active=body.active,
        local=_merge_profile(body.local, stored.local),
        remote=_merge_profile(body.remote, stored.remote),
    )


def _defaults_from_env() -> NerRuntimeState:
    """没有运行时文件时的界面初值：远端项来自 .env，本地项回落默认地址。"""
    s = get_settings()
    active = "remote" if s.HAS_TEXT_RUNTIME.strip().lower() in _REMOTE_ENV_RUNTIMES else "local"
    return NerRuntimeState(
        active=active,
        local=TextModelProfile(
            base_url=s.HAS_LLAMACPP_BASE_URL.strip() or DEFAULT_LOCAL_BASE_URL,
        ),
        remote=TextModelProfile(
            base_url=(s.HAS_TEXT_EXTERNAL_BASE_URL or s.HAS_BASE_URL).strip(),
            api_key=s.HAS_TEXT_API_KEY.strip(),
            model_name=s.HAS_TEXT_MODEL_NAME.strip(),
        ),
    )


def _ui_state() -> NerRuntimeState:
    """设置页展示用：新格式直接展示；旧格式/无文件时与 .env 初值合并。"""
    saved = load_ner_runtime_for_ui()
    defaults = _defaults_from_env()
    if saved is None:
        return defaults
    if load_ner_runtime() is not None:
        return saved
    return defaults.model_copy(
        update={
            "local": TextModelProfile(
                base_url=saved.local.base_url or defaults.local.base_url,
                model_name=saved.local.model_name or defaults.local.model_name,
                display_name=saved.local.display_name,
            )
        }
    )


def _saved_vs_form_hint(profile: TextModelProfile) -> str | None:
    """侧栏健康检查读的是已保存配置；若与当前表单不一致，提示用户。"""
    saved = load_ner_runtime()
    if saved is None:
        return None
    if saved.profile().base_url.rstrip("/") != profile.base_url.rstrip("/"):
        return (
            "【说明】侧栏依据已保存的 API 地址；当前输入框地址与已保存不同，测试结果以输入框为准。"
        )
    return None


def _profile_headers(profile: TextModelProfile) -> dict[str, str] | None:
    api_key = profile.api_key.strip()
    return {"Authorization": f"Bearer {api_key}"} if api_key else None


@router.get("", response_model=NerRuntimeState)
async def get_ner_backend():
    """当前文本模型配置：本地 / 远端两份 + 当前生效项。"""
    return _mask_state(_ui_state())


@router.put("", response_model=NerRuntimeState)
async def put_ner_backend(body: NerRuntimeState):
    """保存两份配置，并把 body.active 设为当前生效项（立即生效，无需重启）。"""
    merged = _merge_state(body, _ui_state())
    if not merged.profile().base_url.strip():
        if merged.active != "local":
            raise HTTPException(status_code=422, detail="远端模型地址不能为空")
        merged.local = merged.local.model_copy(update={"base_url": DEFAULT_LOCAL_BASE_URL})
    _validate_base_url(merged.profile().base_url)
    save_ner_runtime(merged)
    return _mask_state(merged)


@router.delete("")
async def delete_ner_backend():
    """删除运行时配置，恢复为环境变量 / .env 默认值。"""
    removed = delete_ner_runtime()
    message = "已清除前端覆盖，使用环境变量默认" if removed else "没有前端覆盖，当前即环境变量默认"
    return {"ok": True, "message": message}


@router.post("/test")
async def test_ner_backend(body: NerRuntimeState):
    """
    连通性测试（使用请求体中被测页签的配置，无需先保存）。
    依次探测 /v1/models、models、health 等（不同 OpenAI 兼容服务路径不一）。
    """
    merged = _merge_state(body, _ui_state())
    profile = merged.profile(body.active)
    if not profile.base_url.strip():
        raise HTTPException(status_code=422, detail="测试地址不能为空")
    _validate_base_url(profile.base_url)
    hint = _saved_vs_form_hint(profile)
    try:
        ok, probe_message, _used_url, strict = probe_llamacpp(
            profile.base_url,
            timeout=8.0,
            headers=_profile_headers(profile),
        )
        if not ok:
            return {
                "success": False,
                "message": _with_hint("NER 后端连通性测试失败，请检查服务地址和进程状态。", hint),
            }
        if strict:
            ok_msg = "OpenAI 兼容接口正常"
            if probe_message:
                ok_msg = f"{ok_msg}（服务报告模型：{probe_message}）"
        else:
            ok_msg = "NER 后端服务正常"
        return {"success": True, "message": _with_hint(ok_msg, hint)}
    except Exception:
        logger.warning("NER backend connectivity test failed", exc_info=True)
        return {
            "success": False,
            "message": _with_hint("NER 后端连通性测试失败，请检查服务地址和进程状态。", hint),
        }
