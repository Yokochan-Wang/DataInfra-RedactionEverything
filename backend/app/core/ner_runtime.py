"""
文本 NER 运行时配置（优先于环境变量 / .env）

设置页「文本模型」把本地 / 远端两份配置写进 data/ner_backend.json，其中 active
指向当前生效的那一份。推理链路每次都读这里，因此换模型不需要重启、也不需要改 .env。

存储格式（schema 2）：

    {
      "schema": 2,
      "active": "local",
      "local":  {"base_url": ..., "api_key": "", "model_name": ..., "display_name": ...},
      "remote": {"base_url": ..., "api_key": ..., "model_name": ..., "display_name": ...}
    }

旧格式（只有 backend / llamacpp_base_url）在历史上一直被 .env 覆盖（见 config.py
的解析顺序），所以这里同样保持“不生效”：首次在设置页保存后自动升级为 schema 2。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# 本地 OpenAI 兼容服务（llama-server / vLLM）默认地址
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"
# 已保存的 API Key 在读接口里的占位符；写接口收到它就保留服务端原值
REDACTED_API_KEY = "__REDACTED__"


class TextModelProfile(BaseModel):
    """一份文本模型连接配置。本地与远端字段完全一致，只是远端通常需要 api_key。"""

    model_config = ConfigDict(protected_namespaces=())

    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    display_name: str = ""

    def effective_name(self) -> str:
        """主页/侧栏展示名：显式显示名优先，其次模型名。"""
        return (self.display_name or self.model_name or "").strip()


class NerRuntimeState(BaseModel):
    """文本 NER 运行时状态：本地 / 远端各一份配置 + 当前生效项。"""

    model_config = ConfigDict(protected_namespaces=())

    schema_version: Literal[2] = 2
    active: Literal["local", "remote"] = "local"
    local: TextModelProfile = Field(
        default_factory=lambda: TextModelProfile(base_url=DEFAULT_LOCAL_BASE_URL)
    )
    remote: TextModelProfile = Field(default_factory=TextModelProfile)

    # 旧格式兼容字段：只用于读写回显，不参与生效逻辑。
    backend: Literal["llamacpp"] = "llamacpp"
    llamacpp_base_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_payload(cls, data: Any) -> Any:
        """容许旧前端只提交 {backend, llamacpp_base_url}。

        只在**缺少** local / remote 键时才补默认值，否则 Python 侧传入 profile
        对象时会被误判成旧格式而丢掉真实配置。
        """
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        if raw.get("local") is None:
            raw["local"] = {"base_url": raw.get("llamacpp_base_url") or DEFAULT_LOCAL_BASE_URL}
            raw.setdefault("active", "local")
        if raw.get("remote") is None:
            raw["remote"] = {}

        local = raw["local"]
        if isinstance(local, dict):
            base = str(local.get("base_url") or DEFAULT_LOCAL_BASE_URL)
        elif isinstance(local, TextModelProfile):
            base = local.base_url or DEFAULT_LOCAL_BASE_URL
        else:
            base = DEFAULT_LOCAL_BASE_URL
        raw["llamacpp_base_url"] = base
        return raw

    def profile(self, which: str | None = None) -> TextModelProfile:
        """取当前生效（或指定）那一份配置。"""
        key = str(which if which is not None else self.active).strip().lower()
        return self.remote if key == "remote" else self.local


def _path() -> str:
    from app.core.config import get_settings

    return os.path.join(get_settings().DATA_DIR, "ner_backend.json")


def _read_raw() -> dict[str, Any] | None:
    p = _path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("读取 ner_backend.json 失败: %s", e)
        return None
    return raw if isinstance(raw, dict) else None


def _is_current_schema(raw: dict[str, Any]) -> bool:
    return raw.get("schema") == SCHEMA_VERSION and raw.get("active") in {"local", "remote"}


def load_ner_runtime() -> NerRuntimeState | None:
    """读取**生效**的运行时配置。无文件、或仍是旧格式时返回 None（沿用 .env）。"""
    raw = _read_raw()
    if raw is None or not _is_current_schema(raw):
        return None
    try:
        return NerRuntimeState(**raw)
    except Exception as e:
        logger.warning("解析 ner_backend.json 失败: %s", e)
        return None


def load_ner_runtime_for_ui() -> NerRuntimeState | None:
    """读取设置页要展示的配置（旧格式也会迁移后返回），不做生效判定。"""
    raw = _read_raw()
    if raw is None:
        return None
    try:
        return NerRuntimeState(**raw)
    except Exception as e:
        logger.warning("解析 ner_backend.json 失败: %s", e)
        return None


def save_ner_runtime(cfg: NerRuntimeState) -> None:
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = cfg.model_dump()
    payload.pop("schema_version", None)
    payload["schema"] = SCHEMA_VERSION
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def delete_ner_runtime() -> bool:
    p = _path()
    if not os.path.exists(p):
        return False
    os.remove(p)
    return True
