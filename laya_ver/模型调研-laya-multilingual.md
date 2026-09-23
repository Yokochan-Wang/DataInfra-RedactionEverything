# laya-multilingual 调研报告

调研对象：`laya_ver/laya-multilingual`（来源 https://modelscope.cn/models/convaiinnovations/laya-multilingual ）
调研方式：直接读取本地已下载的模型文件 + 反编译官方推理包 `laya==0.3.4` 的源码
结论日期：2026-09-21

---

## 0. 一句话结论

**laya-multilingual 不是生成式大模型，而是一个"决策 / 分类"模型；它不能做本项目需要的实体抽取（NER），
因此无法直接替换当前文本脱敏链路里的 NER 模型。**

具体地说：它没有 `/v1/chat/completions`、不输出文本、也不产生任何字符位置（start / end offset）。
它只能对一段文本回答"是 / 否"或"从固定选项里选一个"，并给出概率。

---

## 1. 它到底是什么

| 项 | 内容 |
|---|---|
| 类型 | 非自回归 **System 1 决策模型**（RLCD 强化学习训练） |
| 架构 | mmBERT-base 编码器（307M，22 层，hidden 768，256k 词表）+ 决策头（2 层 transformer + 选项 marker scorer + act/escalate head），共 322M |
| 参数量 | 322M（本地目录 647 MB） |
| 上下文 | 1024 token（其中问题与选项占 256） |
| 语言 | 100+（MASSIVE 51 语言实测） |
| 许可 | apache-2.0 |
| 调用方式 | **Python 包** `pip install laya`，进程内前向推理；**不是 HTTP 服务** |
| 速度 | 单问题约 33 ms（T4）；10 个问题批量约 72 ms；CPU 约 200–500 ms（慢 10–15 倍） |

### 1.1 本地已下载的文件结构

    laya_ver/laya-multilingual/
    ├── configuration.json          {"framework":"pytorch","task":"text-classification"}
    ├── rl_agent_config.json        模型结构定义（encoder / head_layers / max_len / temperature ...）
    ├── model.safetensors           644 MB，全部权重（含 encoder. / type_emb. / scorer. / act_head. 前缀）
    ├── encoder/config.json         ModernBertForMaskedLM（hidden 768 / 22 层 / 256k 词表）
    ├── tokenizer/tokenizer.json    34 MB
    └── tokenizer/tokenizer_config.json

注意 `encoder/config.json` 里写的是 `ModernBertForMaskedLM`，看着像"掩码语言模型"，
但权重实际上是**决策头**——官方代码 `_verify_compatibility()` 会强制校验
`encoder.` / `type_emb.` / `scorer.` / `act_head.` 四个前缀齐不齐，缺一个就报错，
所以**不能**用 transformers 的常规 pipeline 把它当生成模型跑。

---

## 2. 怎么用（官方 API）

### 2.1 安装

    pip install laya            # 0.3.4；依赖 torch>=2.0, transformers>=4.45, safetensors, numpy
    # 如果环境里装了 TensorFlow，官方 README 建议加 USE_TF=0 避免 abseil 死锁

⚠️ 本项目 `.venv`（Python 3.11.16）目前**没有 torch，也没有 laya**，需要先装（依赖体积约 2–3 GB）。

### 2.2 加载本地目录（离线可用）

    import laya
    agent = laya.load("laya_ver/laya-multilingual")   # Agent() 直接吃本地路径

`Agent.__init__` 的硬性要求（缺一不可）：`rl_agent_config.json`、`model.safetensors`、`encoder/`、`tokenizer/`。
设备选择：有 CUDA 用 bf16 / fp16，否则 CPU fp32。

### 2.3 提问（唯一的功能入口）

    result = agent.predict(
        {"body": "张三，身份证 110101199001011234，电话 13800138000。"},   # state：文本 / dict / list
        {
            "has_person": {"type": "noul",   "instructions": "Does body contain a person's name?"},
            "has_id":     {"type": "noul",   "instructions": "Does body contain an ID number?"},
            "doc_kind":   {"type": "choice", "instructions": "What kind of document is body?",
                           "criteria": {"contract": "合同/协议", "invoice": "发票/票据", "other": "其它"}},
            "risk":       {"type": "score",  "instructions": "How sensitive is body?",
                           "criteria": ["nothing", "mild", "sensitive", "critical"]},
        },
    )
    print(result["answers"]["has_person"]["noul"])   # 0.93 → 概率，不是文本

返回值结构：`{"model": "laya-rl-agent", "answers": ..., "usage": ...}`，
每个 answer 带 `probabilities` / `confidence` / `action.act_probability`。

### 2.4 只有三种问题类型（源码 `laya/common.py`：QTYPES = choice / score / noul）

| type | 语义 | 输出 |
|---|---|---|
| `choice` | 从 `criteria` 字典里选一个标签 | 标签名 + 各选项概率（**选项数建议 ≤ 20**） |
| `score` | 有序等级打分 | 期望分 + 各等级概率 |
| `noul` | 是 / 否 | 正类概率 |

**没有 span / 序列标注 / 抽取类型。** 这是决定性的限制。

---

## 3. 为什么它替换不了本项目的 NER

本项目文本链路对模型的要求（见 `backend/app/services/has_client.py`）：

1. `POST {base_url}/chat/completions`（OpenAI 兼容）→ laya 是 Python 包，**没有 HTTP 接口**；
2. 返回**严格 JSON**，形如 `{"类型": [{"text": "张三", "start": 10, "end": 12}]}`；
3. 下游要拿 `start/end` 去和 OCR 文本框、原文偏移对齐，才能落红框 / 替换；
4. 需要"把实体原文抄出来"的**生成能力**。

laya 四条都不满足：它只输出标签与概率，既不产生文本，也不产生位置。

### 3.1 如果仍想用 laya，可行的两种定位（都需要新写适配层，不是替换）

| 定位 | 做法 | 代价 |
|---|---|---|
| **候选校验器**（二道闸） | NER 先出候选实体，再用 laya `noul` 逐条问"这个是不是人名 / 公司名"，用概率做过滤或排序 | 需新写一个 Python 适配服务 + 把候选批量打包；每候选一次前向（批量很便宜，约 7 ms/问题）；**只能改精度，不能补召回** |
| **前置路由 / 筛查** | 先用 laya `choice` 判断文档里大概有哪些类型的实体，只把这些类型发给 NER，省 token / 时间 | 需新写适配层；收益是省 token，不是替换模型 |

两者都属于**新功能开发**，且都需要先做效果评测，不建议现在做。

### 3.2 真正能"换回本地小模型"的做法

换模型的实际约束只有一条：**目标模型必须能被 OpenAI 兼容服务（llama-server / vLLM）加载，并且能按提示词生成严格的实体 JSON。**

可选路线（按推荐度）：

1. **继续用 HaS Text 0.6B Q4 GGUF**（现在的本地方案）——本来就是按本项目提示词模板训的；
2. **换一个更小的中文 instruct 模型**（如 Qwen3-0.6B / 1.7B 的 GGUF 或 vLLM）——但**必须重写 NER 提示词模板**并做对照评测
   （`has_client.py` 里的模板与 HaS 模型卡逐字对齐：HaS 用 ner/hide/pair/seek 式问答，通用 instruct 模型更适合直接要求输出 JSON）；
3. laya 家族（本次）——**不可行**。

---

## 4. 官方自述的局限（原文要点）

- **未标定**：`temperature = [1.0, 1.0, 1.0]`，系统性过度自信（平均置信度 0.75–0.83 而准确率低得多）。官方建议在自有数据上按 (问题类型, 选项数) 重新拟合温度，平均 ECE 可从 0.314 降到 0.106。
- **英语弱于专用英文版**：0.619 vs 0.684，官方建议用 `Router` 路由而不是直接替换。
- **typed-decisions 零样本接近随机**：0.342（随机基线 0.318，多数类 0.461）——需要针对具体工作流微调才有能力。
- **`choice` 选项数别超过约 20 个**，否则每个标签只剩几个 token，准确率掉得很快。
- 低资源语言依然弱：Swahili 0.210、Tamil 0.250、Amharic 0.110。
- 有序 `score` 是最弱的原语（SST-5 0.282）。

---

## 5. 快速验证脚本

`laya_ver/laya_probe.py` 已就绪（本机尚未安装 torch / laya，装好后可直接跑）：

    cd "C:/Work/WSY/DataInfra-RedactionEverything"
    .venv/Scripts/python.exe -m pip install laya
    USE_TF=0 .venv/Scripts/python.exe laya_ver/laya_probe.py

脚本会用一段中文样例文本问 3 个问题（是否含人名 / 是否含身份证号 / 文档类别），
打印概率与置信度——用来确认模型能加载、能出结果，并直观感受"它给的是概率不是实体"。
