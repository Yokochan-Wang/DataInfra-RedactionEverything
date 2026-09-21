"""长文档 NER 基准 harness：走真实 HTTP 表面采集 BEFORE/AFTER 数据。

流程（与人工操作完全一致，零埋点）：
  1. GET  {prefix}/auth/status            —— 确认 AUTH_ENABLED 与密码初始化状态
  2. POST {prefix}/auth/login | register | setup  —— 拿 Bearer token（按状态回退）
  3. POST {prefix}/files/upload           —— multipart 上传合同语料 txt
  4. GET  {prefix}/files/{id}/parse       —— 触发解析，content 与 corpus 逐字校验
  5. POST {prefix}/files/{id}/ner/hybrid  —— perf_counter 测端到端墙钟；
     请求开始前记录日志行号，请求结束后统计日志窗口内新增的
     "HaS model request finished" 行数 → remote_request_count（即远程模型请求次数）
  6. GET  {prefix}/files/{id}/ner/closed-loop —— 读本轮闭环审计（duration_ms/轮次/剪枝）

输出（out 目录，可清空重建）：
  before_report.json          每文档 wall_ms / remote_request_count / entity_count /
                              实体多重集合 / preamble_gap_ms 等
  entities_{doc}.json          完整实体清单（text/type/start/end/source/coref_id）

preamble_gap_ms = 端点墙钟 − 响应体 closed_loop.duration_ms：
  差额即闭环探测轮之外的端点耗时（认证、文件存取、实体装载、合并落库、序列化等），
  仅诊断记录，不改任何生产代码。

用法（先启动后端，再把日志重定向到 logs/eval-before-backend.log）：
  .venv\Scripts\python.exe backend/scripts/eval/bench_ner_hybrid_endpoint.py \
      --label before
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2].parent
DEFAULT_CORPUS_DIR = SCRIPT_DIR / "artifacts" / "corpus"
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "eval-before-backend.log"

# 后端日志中远程模型请求完成的标志行（has_client.py:185）
MODEL_REQUEST_MARKER = "HaS model request finished"
_MODEL_ELAPSED_RE = re.compile(r"HaS model request finished in (\d+)ms")
_CACHE_HIT = "HaS NER cache hit"


def log(msg: str) -> None:
    print(f"[bench {time.strftime('%H:%M:%S')}] {msg}", flush=True)


class LogWindowCounter:
    """日志窗口计数器：记录请求开始时的行号，结束时统计新增的标志行。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _lines(self) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return []

    def offset(self) -> int:
        return len(self._lines())

    def window(self, start: int) -> tuple[int, list[int], int]:
        """返回 (新增模型请求数, 各请求耗时 ms 列表, 窗口内缓存命中数)。"""
        elapsed: list[int] = []
        cache_hits = 0
        for line in self._lines()[start:]:
            if MODEL_REQUEST_MARKER in line:
                match = _MODEL_ELAPSED_RE.search(line)
                if match:
                    elapsed.append(int(match.group(1)))
            elif _CACHE_HIT in line:
                cache_hits += 1
        return len(elapsed), elapsed, cache_hits


class BenchClient:
    def __init__(self, base_url: str, api_prefix: str, timeout_sec: float) -> None:
        self.base = base_url.rstrip("/")
        self.prefix = api_prefix
        self.http = httpx.Client(
            timeout=httpx.Timeout(timeout_sec, connect=20.0),
            trust_env=False,  # bench 走本机后端，禁用系统代理
        )

    def url(self, path: str) -> str:
        return f"{self.base}{self.prefix}{path}"

    def get(self, path: str, **kwargs) -> dict:
        response = self.http.get(self.url(path), **kwargs)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.http.post(self.url(path), **kwargs)

    def ensure_auth(self, username: str, password: str) -> str:
        """AUTH_ENABLED=true 下拿 token；按运行状态回退 login→register→setup。"""
        status = self.get("/auth/status")
        if not status.get("auth_enabled"):
            log("auth disabled; 跳过登录")
            return ""
        body = {"username": username, "password": password}
        login = self.post("/auth/login", json=body)
        if login.status_code == 200:
            log(f"login ok: {username}")
            return login.json()["access_token"]
        if not status.get("password_set"):
            setup = self.post("/auth/setup", json=body)
            setup.raise_for_status()
            log(f"setup ok（首次初始化管理员 {username}）")
            return setup.json()["access_token"]
        register = self.post("/auth/register", json=body)
        register.raise_for_status()
        log(f"register ok: {username}")
        return register.json()["access_token"]

    def upload(self, path: Path, upload_source: str = "") -> str:
        # upload_source=batch 需要 batch_group_id/job_id；基准是单文件直传，不传该字段
        with open(path, "rb") as handle:
            files = {"file": (path.name, handle.read(), "text/plain; charset=utf-8")}
            data = {"upload_source": upload_source} if upload_source else {}
            response = self.post("/files/upload", files=files, data=data)
        response.raise_for_status()
        return response.json()["file_id"]


def run_doc(client: BenchClient, counter: LogWindowCounter, doc: dict) -> tuple[dict, list[dict]]:
    """单文档：上传 → 解析 → 墙钟计时 NER → 日志窗口计数 → 闭环审计 → 实体 dump。

    doc 为 manifest 中的文档条目（含 file/path/file_text/chars/entity_total）。
    """
    text = doc["file_text"]
    file_id = client.upload(doc["path"])
    log(f"{doc['doc']}: uploaded file_id={file_id}")

    parsed = client.get(f"/files/{file_id}/parse")
    content = parsed.get("content") or ""
    content_matches = content == text
    if not content_matches:
        log(f"{doc['doc']}: WARNING 后端 content 与语料不一致（{len(content)} vs {len(text)} 字符）")

    start_offset = counter.offset()
    started = time.perf_counter()
    response = client.post(f"/files/{file_id}/ner/hybrid")
    wall_ms = round((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    ner = response.json()

    request_count, elapsed_ms, cache_hits = counter.window(start_offset)

    audit = client.get(f"/files/{file_id}/ner/closed-loop").get("closed_loop") or {}
    audit_duration = int(audit.get("duration_ms") or 0)

    entities = ner.get("entities") or []
    multiset: dict[str, int] = {}
    for entity in entities:
        entity_type = str(entity.get("type"))
        multiset[entity_type] = multiset.get(entity_type, 0) + 1

    result = {
        "doc": doc["doc"],
        "file_id": file_id,
        "corpus_chars": doc["chars"],
        "corpus_entities": doc["entity_total"],
        "wall_ms": wall_ms,
        "remote_request_count": request_count,
        "remote_model_ms_list": elapsed_ms,
        "remote_model_ms_total": sum(elapsed_ms),
        "log_cache_hits": cache_hits,
        "entity_count": ner.get("entity_count", len(entities)),
        "entity_multiset": multiset,
        "parse_content_matches_corpus": content_matches,
        "closed_loop_duration_ms": audit_duration,
        "preamble_gap_ms": wall_ms - audit_duration,
        "rounds_run": audit.get("rounds_run"),
        "termination_reason": audit.get("termination_reason"),
        "rounds": audit.get("rounds"),
        "warnings": ner.get("warnings"),
        "recognition_failed": ner.get("recognition_failed", False),
        "error": ner.get("error"),
    }
    log(
        f"{doc['doc']}: wall={wall_ms}ms requests={request_count}"
        f" entities={result['entity_count']} closed_loop={audit_duration}ms"
        f" preamble_gap={result['preamble_gap_ms']}ms rounds={audit.get('rounds_run')}"
        f" ({audit.get('termination_reason')})"
    )
    return result, entities


def main() -> None:
    parser = argparse.ArgumentParser(description="NER hybrid 端点基准 harness（真实 HTTP）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-prefix", default="/api/v1", help="settings.API_PREFIX")
    parser.add_argument("--corpus-dir", default=str(DEFAULT_CORPUS_DIR))
    parser.add_argument("--out-dir", default=str(SCRIPT_DIR / "artifacts" / "before"))
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH), help="后端日志文件（日志窗口计数用）")
    parser.add_argument("--label", default="before")
    parser.add_argument("--username", default="ner_eval_bench")
    parser.add_argument("--password", default="Ner_Eval_2026a")
    parser.add_argument("--docs", default="doc_a,doc_b,doc_c")
    parser.add_argument("--timeout-sec", type=float, default=900.0, help="单请求读超时（长文档串行多轮，需宽裕）")
    args = parser.parse_args()

    manifest_path = Path(args.corpus_dir) / "manifest.json"
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    docs_by_name = {item["doc"]: item for item in manifest["docs"]}
    wanted = [name.strip() for name in args.docs.split(",") if name.strip()]
    missing = [name for name in wanted if name not in docs_by_name]
    if missing:
        raise SystemExit(f"manifest 中找不到文档：{', '.join(missing)}")
    for item in docs_by_name.values():
        item["path"] = Path(args.corpus_dir) / item["file"]
        item["file_text"] = item["path"].read_text(encoding="utf-8")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.iterdir():
        if stale.is_file():
            stale.unlink()

    client = BenchClient(args.base_url, args.api_prefix, args.timeout_sec)

    # /health 与 /health/services 挂在根路径（无 API_PREFIX），其余走前缀
    health = client.http.get(f"{client.base}/health")
    health.raise_for_status()
    health = health.json()
    log(f"health: {json.dumps(health, ensure_ascii=False)}")
    services = {}
    try:
        services_response = client.http.get(f"{client.base}/health/services")
        services_response.raise_for_status()
        services = services_response.json()
        has = (services.get("services") or {}).get("has_ner") or {}
        log(f"health/services: has_ner={json.dumps(has, ensure_ascii=False)}")
    except Exception as exc:  # 仅诊断信息，不影响主流程
        log(f"WARNING /health/services 读取失败：{exc}")

    token = client.ensure_auth(args.username, args.password)
    if token:
        client.http.headers["Authorization"] = f"Bearer {token}"

    counter = LogWindowCounter(Path(args.log_path))
    if not counter.path.exists():
        log(f"WARNING 日志文件不存在：{counter.path}（--log-path 指定后端日志，否则 request_count=0）")

    report = {
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "branch_note": "perf/text-ner-acceleration BEFORE 基线（未改动 app/ 下生产代码）",
        "backend": {"base_url": args.base_url, "api_prefix": args.api_prefix, "health": health, "services_health": services},
        "auth": {"username": args.username, "authenticated": bool(token)},
        "log_path": str(counter.path),
        "model_request_marker": MODEL_REQUEST_MARKER,
        "default_generic_types": manifest.get("default_generic_types"),
        "docs": [],
    }

    for name in wanted:
        doc = docs_by_name[name]
        result, entities = run_doc(client, counter, doc)
        entities_path = out_dir / f"entities_{name}.json"
        with open(entities_path, "w", encoding="utf-8") as handle:
            json.dump(entities, handle, ensure_ascii=False, indent=2)
        result["entities_file"] = entities_path.name
        report["docs"].append(result)

    report["summary"] = {
        "docs": len(report["docs"]),
        "wall_ms_total": sum(item["wall_ms"] for item in report["docs"]),
        "remote_request_count_total": sum(item["remote_request_count"] for item in report["docs"]),
        "entity_count_total": sum(item["entity_count"] for item in report["docs"]),
        "preamble_gap_ms_total": sum(item["preamble_gap_ms"] for item in report["docs"]),
    }

    report_path = out_dir / f"{args.label}_report.json"
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    log(f"summary: {json.dumps(report['summary'], ensure_ascii=False)}")
    log(f"report -> {report_path}")


if __name__ == "__main__":
    main()
