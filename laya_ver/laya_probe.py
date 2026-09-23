"""laya-multilingual 快速验证脚本（本机尚未安装 torch/laya 时不能运行）。

用途：确认本地权重能被官方 laya 包加载，并直观看到它的输出是"概率 + 标签"而不是实体文本。

准备：
    C:/Work/WSY/DataInfra-RedactionEverything/.venv/Scripts/python.exe -m pip install laya

运行（Windows Git Bash）：
    USE_TF=0 .venv/Scripts/python.exe laya_ver/laya_probe.py

注意：官方 README 说明，若环境里装了 TensorFlow，transformers 导入时的 TF 探测会死锁，
因此务必带上 USE_TF=0。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "laya-multilingual"

SAMPLE = {
    "body": (
        "用工单位：某某科技有限公司（统一社会信用代码 91310101MA1FL0000X）。"
        "职工姓名：张三，身份证号 110101199001011234，联系电话 13800138000，"
        "居住地址：上海市浦东新区世纪大道 100 号 5 号楼 302 室。"
        "月工资人民币 12,000 元，合同编号 HT-2026-0417。"
    )
}

QUESTIONS = {
    "has_person_name": {
        "type": "noul",
        "instructions": "Does `body` contain a person's name?",
    },
    "has_id_number": {
        "type": "noul",
        "instructions": "Does `body` contain a Chinese resident ID number?",
    },
    "has_address": {
        "type": "noul",
        "instructions": "Does `body` contain a street address?",
    },
    "document_kind": {
        "type": "choice",
        "instructions": "What kind of document is `body`?",
        "criteria": {
            "labor_contract": "劳动合同、用工协议",
            "invoice": "发票、票据、付款凭证",
            "id_card": "身份证件",
            "other": "其它",
        },
    },
    "sensitivity": {
        "type": "score",
        "instructions": "How sensitive is the personal information in `body`?",
        "criteria": ["nothing personal", "mild", "clearly personal", "highly sensitive"],
    },
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not MODEL_DIR.is_dir():
        print(f"[probe] 找不到模型目录: {MODEL_DIR}")
        return 2

    os.environ.setdefault("USE_TF", "0")
    try:
        import laya
    except ImportError:
        print("[probe] 未安装 laya。请先执行：")
        print("    .venv/Scripts/python.exe -m pip install laya")
        return 3

    print(f"[probe] 加载模型：{MODEL_DIR}")
    agent = laya.load(str(MODEL_DIR))
    print(f"[probe] 设备：{agent.device}   dtype：{agent.dtype}")

    result = agent.predict(SAMPLE, QUESTIONS)
    print("\n[probe] 原始返回：")
    for qid, answer in result["answers"].items():
        print(f"  - {qid}: {answer}")

    print("\n[probe] 结论：以上全部是标签与概率，没有任何实体文本或字符位置。")
    print("[probe] 本项目的 NER 需要 {text, start, end}，因此 laya 不能直接替换文本模型。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
