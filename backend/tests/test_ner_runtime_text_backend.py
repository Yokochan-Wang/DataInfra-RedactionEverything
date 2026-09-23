"""文本模型运行时配置：本地 / 远端双档 + 当前生效项。

设置页保存什么，推理链路就用什么：地址、模型名、API Key、主页显示名全部由
data/ner_backend.json 决定，且优先级高于 .env。
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from app.core import auth, health_checks, ner_runtime
from app.core.config import (
    configured_text_display_name,
    get_has_chat_base_url,
    get_has_display_name,
    get_has_text_headers,
    get_has_text_model_name,
    is_remote_text_runtime,
)
from app.core.config import settings
from app.main import app

client = TestClient(app)

REMOTE_URL = "http://remote.example:9000/v1"
LOCAL_URL = "http://127.0.0.1:18080/v1"


@pytest.fixture(autouse=True)
def _scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_AUTH_FILE", str(tmp_path / "auth.json"))
    # 本地地址解析会做真实 TCP/ WSL 探测；单测里短路掉，保持毫秒级且确定。
    monkeypatch.setattr("app.core.config._tcp_connects", lambda *a, **k: True)
    yield


def _headers(role: str = "super_admin") -> dict[str, str]:
    username = f"u_{role}"
    auth.create_user(username, "Passw0rd!", role=role)
    return {"Authorization": f"Bearer {auth.create_token(username)}"}


def _write_raw(payload: dict) -> None:
    path = ner_runtime._path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _save(*, active: str, local: dict, remote: dict) -> None:
    ner_runtime.save_ner_runtime(
        ner_runtime.NerRuntimeState(
            active=active,
            local=ner_runtime.TextModelProfile(**local),
            remote=ner_runtime.TextModelProfile(**remote),
        )
    )


def test_legacy_single_url_file_stays_inert_until_resaved(monkeypatch):
    """旧格式文件历史上一直被 .env 覆盖，升级后同样不生效，避免静默换端点。"""
    _write_raw({"backend": "llamacpp", "llamacpp_base_url": LOCAL_URL})
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external")
    monkeypatch.setattr(settings, "HAS_TEXT_EXTERNAL_BASE_URL", REMOTE_URL)

    assert ner_runtime.load_ner_runtime() is None
    assert get_has_chat_base_url() == REMOTE_URL
    assert is_remote_text_runtime() is True

    ui = ner_runtime.load_ner_runtime_for_ui()
    assert ui is not None and ui.local.base_url == LOCAL_URL


def test_saved_remote_profile_drives_every_resolution(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external")
    monkeypatch.setattr(settings, "HAS_TEXT_EXTERNAL_BASE_URL", "http://stale.example:1/v1")
    monkeypatch.setattr(settings, "HAS_TEXT_MODEL_NAME", "stale-model")
    monkeypatch.setattr(settings, "HAS_TEXT_API_KEY", "stale-key")

    _save(
        active="remote",
        local={"base_url": LOCAL_URL},
        remote={"base_url": REMOTE_URL, "api_key": "fresh-key", "model_name": "aaa"},
    )

    assert get_has_chat_base_url() == REMOTE_URL
    assert get_has_text_model_name() == "aaa"
    assert get_has_text_headers() == {"Authorization": "Bearer fresh-key"}
    assert get_has_display_name() == "aaa"
    assert configured_text_display_name() == "aaa"
    assert is_remote_text_runtime() is True


def test_switching_back_to_local_drops_the_remote_key_and_model(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_API_KEY", "stale-key")
    monkeypatch.setattr(settings, "HAS_TEXT_MODEL_NAME", "stale-model")

    _save(
        active="local",
        local={"base_url": LOCAL_URL, "model_name": "local-small"},
        remote={"base_url": REMOTE_URL, "api_key": "fresh-key", "model_name": "aaa"},
    )

    assert is_remote_text_runtime() is False
    assert get_has_chat_base_url() == LOCAL_URL
    assert get_has_text_model_name() == "local-small"
    # 本地服务不鉴权：绝不能把远端档位的 Key 发到本机。
    assert get_has_text_headers() == {}
    assert get_has_display_name() == "local-small"


def test_local_profile_without_model_name_does_not_fall_back_to_env(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_API_KEY", "stale-key")
    monkeypatch.setattr(settings, "HAS_TEXT_MODEL_NAME", "stale-model")
    _save(active="local", local={"base_url": LOCAL_URL}, remote={})

    assert get_has_text_model_name() == ""
    assert get_has_text_headers() == {}


def test_display_name_wins_over_model_name():
    _save(
        active="local",
        local={"base_url": LOCAL_URL, "model_name": "aaa", "display_name": "甲方专用 0.6B"},
        remote={},
    )
    assert get_has_display_name() == "甲方专用 0.6B"
    assert get_has_text_model_name() == "aaa"


def test_health_reports_the_selected_model_name(monkeypatch):
    _save(active="local", local={"base_url": LOCAL_URL, "model_name": "aaa"}, remote={})
    monkeypatch.setattr(
        "app.core.llamacpp_probe.probe_llamacpp",
        lambda *a, **k: (True, "server-reported-name", LOCAL_URL, True),
    )
    assert health_checks.check_has_ner_health().name == "aaa"


def test_health_services_endpoint_reports_the_selected_model_name(monkeypatch):
    """/health/services 是主页与侧栏名称的来源：保存成什么，这里就回什么。"""
    from app.core.health_checks import ServiceHealth

    _save(active="local", local={"base_url": LOCAL_URL, "model_name": "aaa"}, remote={})
    monkeypatch.setattr(
        "app.core.llamacpp_probe.probe_llamacpp",
        lambda *a, **k: (True, "server-reported-name", LOCAL_URL, True),
    )
    monkeypatch.setattr(
        "app.main.check_ocr_health_sync", lambda *a, **k: ServiceHealth(name="PaddleOCR", status="offline")
    )
    monkeypatch.setattr(
        "app.main.check_service_health_sync",
        lambda *a, **k: ServiceHealth(name="Visual Features", status="offline"),
    )
    monkeypatch.setattr("app.main._query_gpu_memory", lambda *a, **k: None)
    monkeypatch.setattr("app.main._query_gpu_memory_all", lambda *a, **k: None)

    resp = client.get("/health/services")
    assert resp.status_code == 200, resp.text
    assert resp.json()["services"]["has_ner"]["name"] == "aaa"


def test_api_round_trip_masks_key_and_preserves_it_on_resave():
    headers = _headers()
    body = {
        "active": "remote",
        "local": {"base_url": LOCAL_URL, "model_name": "local-small"},
        "remote": {"base_url": REMOTE_URL, "model_name": "aaa", "api_key": "sk-secret"},
    }
    saved = client.put("/api/v1/ner-backend", headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["remote"]["api_key"] == ner_runtime.REDACTED_API_KEY
    assert get_has_text_headers() == {"Authorization": "Bearer sk-secret"}

    masked = client.get("/api/v1/ner-backend", headers=headers).json()
    assert masked["active"] == "remote"
    assert masked["remote"]["api_key"] == ner_runtime.REDACTED_API_KEY
    assert masked["local"]["model_name"] == "local-small"

    # 前端把哨兵值原样回传：服务端保留原 Key，而不是把 "__REDACTED__" 存成新 Key。
    masked["remote"]["model_name"] = "bbb"
    again = client.put("/api/v1/ner-backend", headers=headers, json=masked)
    assert again.status_code == 200, again.text
    assert get_has_text_model_name() == "bbb"
    assert get_has_text_headers() == {"Authorization": "Bearer sk-secret"}


def test_api_accepts_legacy_body_and_switch_back():
    headers = _headers()
    legacy = client.put(
        "/api/v1/ner-backend",
        headers=headers,
        json={"backend": "llamacpp", "llamacpp_base_url": LOCAL_URL},
    )
    assert legacy.status_code == 200, legacy.text

    # 保存“本地模型”页签 = 切回本地小模型，远端档位不受影响。
    switched = client.put(
        "/api/v1/ner-backend",
        headers=headers,
        json={
            "active": "local",
            "local": {"base_url": LOCAL_URL, "model_name": "aaa"},
            "remote": {"base_url": REMOTE_URL, "model_name": "qwen3.8-27b", "api_key": "sk-x"},
        },
    )
    assert switched.status_code == 200, switched.text
    assert is_remote_text_runtime() is False
    assert get_has_chat_base_url() == LOCAL_URL

    after = client.get("/api/v1/ner-backend", headers=headers).json()
    assert after["remote"]["model_name"] == "qwen3.8-27b"


def test_api_keeps_a_known_remote_url_when_the_field_is_cleared():
    """清空地址不会抹掉已知的远端地址，避免误操作后推理链路指向空地址。"""
    headers = _headers()
    client.put(
        "/api/v1/ner-backend",
        headers=headers,
        json={
            "active": "remote",
            "local": {"base_url": LOCAL_URL},
            "remote": {"base_url": REMOTE_URL, "model_name": "aaa"},
        },
    )
    resp = client.put(
        "/api/v1/ner-backend",
        headers=headers,
        json={
            "active": "remote",
            "local": {"base_url": LOCAL_URL},
            "remote": {"base_url": "", "model_name": "aaa"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert get_has_chat_base_url() == REMOTE_URL


def test_api_rejects_remote_profile_without_any_known_base_url(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external")
    monkeypatch.setattr(settings, "HAS_TEXT_EXTERNAL_BASE_URL", "")
    monkeypatch.setattr(settings, "HAS_BASE_URL", "")
    resp = client.put(
        "/api/v1/ner-backend",
        headers=_headers(),
        json={"active": "remote", "local": {"base_url": LOCAL_URL}, "remote": {"model_name": "aaa"}},
    )
    assert resp.status_code == 422, resp.text


def test_delete_restores_env_defaults(monkeypatch):
    monkeypatch.setattr(settings, "HAS_TEXT_RUNTIME", "external")
    monkeypatch.setattr(settings, "HAS_TEXT_EXTERNAL_BASE_URL", REMOTE_URL)
    monkeypatch.setattr(settings, "HAS_TEXT_MODEL_NAME", "env-model")
    _save(active="local", local={"base_url": LOCAL_URL, "model_name": "aaa"}, remote={})
    assert get_has_text_model_name() == "aaa"

    headers = _headers()
    assert client.delete("/api/v1/ner-backend", headers=headers).status_code == 200
    assert ner_runtime.load_ner_runtime() is None
    assert get_has_text_model_name() == "env-model"
    assert get_has_chat_base_url() == REMOTE_URL
