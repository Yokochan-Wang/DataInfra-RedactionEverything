# laya_ver —— 文本脱敏模型：本地 / 远端可切换工作区

本目录是"把文本 NER 从远端模型改回本地小模型"这件事的工作区：模型权重、调研报告、
改动记录、验证脚本都在这里。

## 目录内容

| 文件 / 目录 | 说明 |
|---|---|
| `laya-multilingual/` | 你下载的 laya-multilingual 模型（647 MB，已在 .gitignore 中排除） |
| `模型调研-laya-multilingual.md` | **先读这个**：laya 是什么、怎么用、为什么不能直接替换 NER |
| `laya_probe.py` | laya 加载 + 推理的最小验证脚本（本机尚未装 torch） |
| `改动记录.md` | 本次改造逐文件清单、行为变化与回滚方法 |
| `README.md` | 本文件 |

## 重要说明：代码为什么没有放进 laya_ver/

你要求"之后的代码都存入 laya_ver"。实际改造必须改动应用本体
（`backend/`、`frontend/`）——把代码复制一份到 `laya_ver` 会造成两个互不同步的
副本，应用跑的还是旧代码。所以采取的做法是：

- **功能代码**：原地修改 `backend/` 与 `frontend/`（清单见 `改动记录.md`）；
- **本目录**：存放模型、调研结论、改动记录、回滚步骤与验证脚本。

即：`laya_ver` 承载"这件事的知识与实验资产"，应用代码仍只有一份。

## 本次实现的能力（需求 2）

设置页的「文本识别服务」现在是**两个页签**：

- **本地模型**：本机 / 局域网的 OpenAI 兼容服务（llama-server、vLLM）
- **远程模型**：远端 OpenAI 兼容服务（GPU 服务器 / 云 API），可填 API Key

每个页签各有：地址、模型名称、显示名称（可选）、API Key（仅远程）。
点 **保存并启用** 后：

1. 该页签的配置成为**当前生效**配置（写入 `backend/data/ner_backend.json`）；
2. 推理链路下一次请求就用新地址 / 新模型名，**不需要重启、不需要改 .env**；
3. 主页与侧栏「本地服务」里那一条的名字**立即变成你填的模型名**
   （例如模型名填 `aaa`，主页就显示 `aaa`）。

### 使用步骤

1. 启动应用，进入 **设置 → 文本识别服务**；
2. 打开「远程模型」页签 → 点 **测试** 确认远端连通 → 点 **保存并启用**；
   或打开「本地模型」页签 → 填 `http://127.0.0.1:8080/v1` 与本地模型名 → 测试 → 保存并启用；
3. 回主页看「本地服务」卡片，名称应等于你填的模型名；
4. 随时在两个页签之间来回切，各自配置不会互相覆盖。

### 存储格式（`backend/data/ner_backend.json`）

```json
{
  "schema": 2,
  "active": "remote",
  "local":  { "base_url": "http://127.0.0.1:8080/v1", "api_key": "", "model_name": "HaS_Text_0209_0.6B_Q4", "display_name": "" },
  "remote": { "base_url": "http://4090-48g.zlattice.top:56666/v1", "api_key": "sk-...", "model_name": "qwen3.8-27b", "display_name": "" }
}
```

- `active` 决定真正生效的是 `local` 还是 `remote`；
- `display_name` 留空时用 `model_name` 作为主页显示名；
- 读接口不回传明文 Key（返回 `__REDACTED__`）；把该哨兵值原样写回表示"不修改"，写空字符串表示删除；
- 旧格式文件（只有 `llamacpp_base_url`）**保持不生效**（它历史上一直被 .env 覆盖），
  第一次在设置页保存后自动升级成上面的 schema 2。

### 生效优先级

```
data/ner_backend.json（设置页保存） > .env / 环境变量
```

即：设置页保存过就以设置页为准；点「恢复为环境默认值」删除该文件后，回到 .env 的配置。
因此 **.env 里原有的远端配置原样保留**，天然是回滚点。

### API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/ner-backend` | 返回两份配置 + 当前生效项（Key 脱敏） |
| PUT | `/api/v1/ner-backend` | 保存两份配置，并把 `active` 设为生效项 |
| POST | `/api/v1/ner-backend/test` | 用请求体里被测页签的配置做连通性测试 |
| DELETE | `/api/v1/ner-backend` | 删除覆盖，恢复 .env 默认 |

均需 `super_admin`，并沿用 `NER_BACKEND_HOST_ALLOWLIST` 的 SSRF 白名单校验。

## 验证

```bash
# 后端（本目录新增的运行时配置单测 + 回归）
cd backend
"C:/Work/WSY/DataInfra-RedactionEverything/.venv/Scripts/python.exe" -m pytest \
  tests/test_ner_runtime_text_backend.py tests/test_role_matrix.py \
  tests/test_gpu_inference_gate.py tests/test_gpu_inference_gate_runtime_20260902.py \
  tests/test_closed_loop_recognition_20260902.py tests/test_closed_loop_rounds_runtime_20260902.py \
  tests/test_has_text_ner_concurrency.py -q

# 前端
cd ../frontend && npx tsc --noEmit && npx vitest run
```

已知与本改动无关的既有失败（未提交的 WIP 遗留）：`tests/test_vision_quality_filters.py`
中的 3 个用例（VL 文本块切分），用 `--noconftest` 也能复现。

## 回滚

1. **只回滚模型配置**：设置页点「恢复为环境默认值」，或删除 `backend/data/ner_backend.json`；
2. **回滚代码**：见 `改动记录.md` 末尾的逐文件列表（`git checkout -- <path>`，新增文件直接删除）。
