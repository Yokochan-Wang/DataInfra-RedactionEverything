"""长文档 NER 基准语料生成器：确定性中文合同风格 txt + 植入实体 manifest。

用途：perf/text-ner-acceleration 分支的 BEFORE/AFTER 采集共用同一份语料，
保证两次测的是同一份文本（否则 wall_ms 不可比）。

设计约定：
1. 纯确定性：所有取值来自 random.Random(文档种子)，同一种子字节级复现，可重复运行。
2. offset 约定：manifest 中 start/end 为 txt 文件内的 Python 字符下标（utf-8 解码后）；
   后端 _parse_txt 按 utf-8 原样读入，content 与文件逐字一致，offset 可直接对齐。
3. 覆盖范围：以 entity_type_service.get_default_generic_types 的实际启用集为准——
   脚本读取同一数据源 backend/config/preset_entity_types.json，按
   "enabled and default_enabled and 非 custom_ 前缀"过滤（与该函数同一过滤逻辑），
   生成后逐文档校验全覆盖（校验失败直接报错退出）。
4. 合同编号/金额/统一社会信用代码/银行账号/开户行等默认 schema 之外的植入项一并记录，
   标记 in_default_schema=false，供 S2 精确率评估区分"应召回"与"范围外"实体。

用法（仓库 .venv）：
  .venv\Scripts\python.exe backend/scripts/eval/gen_ner_contract_corpus.py
  .venv\Scripts\python.exe backend/scripts/eval/gen_ner_contract_corpus.py --out-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = SCRIPT_DIR / "artifacts" / "corpus"
# entity_type_service 的预制数据源（PRESET_ENTITY_TYPES 同源）
PRESET_JSON = SCRIPT_DIR.parents[1] / "config" / "preset_entity_types.json"

SEED_BASE = 20260920

# 三个文档：规模递增、实体密度递减（doc_a 最密，doc_c 最稀）
DOC_SPECS = [
    {"doc": "doc_a", "seed_offset": 1, "target_chars": 16000, "density": "dense"},
    {"doc": "doc_b", "seed_offset": 2, "target_chars": 17600, "density": "medium"},
    {"doc": "doc_c", "seed_offset": 3, "target_chars": 19200, "density": "sparse"},
]

# ── 取值池（纯数据） ────────────────────────────────────────────

# (中文姓名, 邮箱拼音前缀)
NAME_POOL = (
    ("张伟", "zhangwei"), ("王芳", "wangfang"), ("李娜", "lina"), ("刘洋", "liuyang"),
    ("陈静", "chenjing"), ("杨磊", "yanglei"), ("黄敏", "huangmin"), ("赵军", "zhaojun"),
    ("周杰", "zhoujie"), ("吴涛", "wutao"), ("徐明", "xuming"), ("孙艳", "sunyan"),
    ("马丽", "mali"), ("朱霞", "zhuxia"), ("胡刚", "hugang"), ("郭华", "guohua"),
    ("何辉", "hehui"), ("林鹏", "linpeng"), ("罗成", "luocheng"), ("高远", "gaoyuan"),
    ("郑雪", "zhengxue"), ("梁晓东", "liangxiaodong"), ("谢文", "xiewen"), ("宋佳", "songjia"),
    ("唐磊", "tanglei"), ("许晴", "xuqing"), ("韩雪", "hanxue"), ("冯洁", "fengjie"),
    ("邓超", "dengchao"), ("曹颖", "caoying"), ("彭辉", "penghui"), ("肖婷", "xiaoting"),
    ("田雨", "tianyu"), ("董强", "dongqiang"), ("潘军", "panjun"), ("袁莉", "yuanli"),
    ("蔡明", "caiming"), ("蒋欣", "jiangxin"), ("余伟", "yuwei"), ("杜鹏", "dupeng"),
    ("叶凡", "yefan"), ("程琳", "chenglin"), ("苏芮", "surui"), ("魏东", "weidong"),
    ("吕婷", "lvting"), ("任重", "renzhong"), ("沈悦", "shenyue"), ("姚远", "yaoyuan"),
    ("卢静", "lujing"), ("姜文", "jiangwen"), ("崔健", "cuijian"), ("钟楠", "zhongnan"),
    ("谭凯", "tankai"), ("陆遥", "luyao"), ("汪涵", "wanghan"), ("范玮", "fanwei"),
    ("金石", "jinshi"), ("廖凡", "liaofan"), ("贾明", "jiaming"), ("夏楠", "xianan"),
    ("韦琪", "weiqi"), ("方圆", "fangyuan"), ("白冰", "baibing"), ("邹勇", "zouyong"),
)

# 城市 → (辖区, 街道)：保证地址/支行里的行政区与城市自洽
CITY_GEO = {
    "深圳市": (("南山区", "福田区", "宝安区", "龙岗区"), ("科技园南路", "深南大道", "高新一路", "建设北路")),
    "广州市": (("天河区", "越秀区", "海珠区", "黄埔区"), ("中山路", "体育东路", "天河北路", "黄埔大道")),
    "北京市": (("朝阳区", "海淀区", "东城区", "西城区"), ("中关村大街", "建国路", "金融大街", "学院路")),
    "上海市": (("浦东新区", "徐汇区", "静安区", "黄浦区"), ("世纪大道", "南京东路", "淮海路", "张杨路")),
    "杭州市": (("西湖区", "滨江区", "余杭区", "上城区"), ("凤起路", "文一路", "滨盛路", "庆春路")),
    "南京市": (("鼓楼区", "玄武区", "秦淮区", "江宁区"), ("中山路", "北京东路", "长江路", "金阊路")),
    "成都市": (("武侯区", "锦江区", "青羊区", "成华区"), ("天府大道", "春熙路", "一环路", "建设路")),
    "武汉市": (("武昌区", "江汉区", "洪山区", "汉阳区"), ("中南路", "解放路", "光谷大道", "建设大道")),
    "西安市": (("雁塔区", "碑林区", "莲湖区", "未央区"), ("高新路", "长安路", "友谊路", "科技路")),
    "苏州市": (("姑苏区", "吴中区", "虎丘区", "相城区"), ("干将路", "三香路", "长江路", "阳澄湖路")),
}
BRANDS = ("恒瑞达", "华信远", "中衡", "金桥", "联创", "弘泰", "云图", "智擎", "博远", "盛弘", "宏利", "瑞安", "东方汇", "泉盛", "泰和中", "新元")
INDUSTRIES = ("科技", "信息技术", "网络科技", "智能科技", "数据服务", "软件", "电子商务", "文化传媒", "实业", "建设工程", "咨询服务", "生物科技", "新能源", "物流供应链")
ORG_FORMS = ("有限公司", "股份有限公司", "有限责任公司", "集团有限公司")
BANKS = ("中国工商银行", "中国建设银行", "中国农业银行", "中国银行", "招商银行", "平安银行", "中信银行", "交通银行", "中国民生银行")
DOMAINS = ("163.com", "126.com", "qq.com", "foxmail.com", "gmail.com", "outlook.com", "sina.com")
ID_REGIONS = ("110101", "110105", "310115", "440305", "440304", "510107", "330106", "320206", "420111", "610113", "120103", "500105")
MOBILE_PREFIX = ("139", "138", "137", "136", "135", "186", "188", "187", "185", "183", "177", "199", "198", "166", "159", "158", "150", "151")
CARD_BINS = ("622202", "622208", "621700", "622848", "621661", "621226", "622588", "622700", "621483", "622155")
PASSPORT_PREFIX = ("E", "G", "D", "S", "P")
CONTRACT_KINDS = ("技术服务合同", "技术开发合同", "采购供应合同", "咨询服务合同", "系统集成合同", "软件开发合同")

# 正文填充条款：只含法律表述，不含任何 PII（避免注入未登记实体）
_FILLER_CLAUSES = (
    "双方确认，本协议的磋商过程与最终文本均以书面形式为准，任何口头承诺不构成对本协议条款的变更。本协议签订前双方相互传递的全部意向性文本，自本协议生效之日起自动失效，不再具有约束力。",
    "一方不得以未尽事宜为由拒绝履行本协议项下已明确的义务。确需补充约定的事项，应由双方协商一致后以书面补充文本确认，补充文本与本协议具有同等效力，冲突时以签署时间在后者为准。",
    "本协议项下的全部通知与文件送达，均以书面形式发送至双方列明的联络渠道；以电子方式送达的，送达时间以系统记录的发送成功时间为准；以专人递送的，以收件方签收时间为准。",
    "双方各自的清算、合并、分立或重组，不影响本协议项下已产生的权利义务的承继与履行。承继方应继续全面履行本协议，并书面通知对方相关变更情况。",
    "因本协议产生的全部税费，由双方按照法律法规的规定各自承担；法律法规没有明确规定的，由双方平均分担。双方应相互配合提供必需的票据与证明材料。",
    "本协议的中外文文本如有歧义，以中文文本为准；双方另行书面约定以其他文本为准的，从其约定。本协议附件、附图及双方确认的往来函件均为本协议不可分割的组成部分。",
    "任何一方违反保密义务的，应赔偿由此给对方造成的全部实际损失，包括但不限于合理的调查取证支出。双方对商业秘密的保密义务不因本协议的解除或终止而失效。",
    "本协议签订后，双方此前就同一事项达成的全部文本自动失效。履行过程中形成的会议纪要、备忘录等文件与本协议不一致的，以本协议为准。",
    "双方应本着诚实信用的原则行使权利、履行义务，不得滥用优势地位或者相对方的信赖损害对方合法权益。显失公平的条款受损害方有权请求予以变更或者撤销。",
    "本协议项下的争议，无论协商、调解或者仲裁程序进行到何种程度，均不影响其余条款的继续履行；争议事项的暂缓履行应取得对方书面同意。",
    "双方任一连络方式发生变更的，应提前五个工作日书面告知对方；因未及时告知导致送达不能的，以原联络方式发出的通知视为有效送达。",
    "本协议的任何条款被认定为无效或者不可执行的，不影响其余条款的效力，双方应以最接近原条款目的的有效条款予以替代。",
)


class _DocBuilder:
    """按字符偏移记录植入实体的文档装配器。"""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self.entities: list[dict] = []

    def write(self, text: str) -> None:
        self._parts.append(text)
        self._length += len(text)

    def write_entity(self, text: str, entity_type: str) -> None:
        start = self._length
        self.write(text)
        self.entities.append({"text": text, "type": entity_type, "start": start, "end": self._length})

    def render(self) -> str:
        return "".join(self._parts)

    @property
    def length(self) -> int:
        return self._length


# ── 取值生成（全部由 rng 驱动，确定性） ─────────────────────────

def _digits(rng: random.Random, count: int) -> str:
    return "".join(str(rng.randrange(10)) for _ in range(count))


def _person_name(rng: random.Random) -> str:
    return rng.choice(NAME_POOL)[0]


def _email(rng: random.Random, name: str) -> str:
    pinyin = next(pair[1] for pair in NAME_POOL if pair[0] == name)
    return f"{pinyin}{rng.randrange(100, 999)}@{rng.choice(DOMAINS)}"


def _id_card(rng: random.Random) -> str:
    """GB 11643 校验位正确的 18 位身份证号。"""
    body = rng.choice(ID_REGIONS)
    body += f"{rng.randrange(1965, 1996)}{rng.randrange(1, 13):02d}{rng.randrange(1, 29):02d}"
    body += _digits(rng, 3)
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checksum = "10X98765432"[sum(int(b) * w for b, w in zip(body, weights)) % 11]
    return body + checksum


def _phone(rng: random.Random) -> str:
    return rng.choice(MOBILE_PREFIX) + _digits(rng, 8)


def _passport(rng: random.Random) -> str:
    return rng.choice(PASSPORT_PREFIX) + _digits(rng, 8)


def _address(rng: random.Random) -> str:
    city = rng.choice(tuple(CITY_GEO))
    districts, streets = CITY_GEO[city]
    return (
        city
        + rng.choice(districts)
        + rng.choice(streets)
        + f"{rng.randrange(1, 999)}号"
        + f"{rng.randrange(1, 30)}层{rng.randrange(1, 12)}{rng.randrange(1, 20):02d}室"
    )


def _bank_card(rng: random.Random) -> str:
    """Luhn 校验位正确的 19 位银行卡号。"""
    body = rng.choice(CARD_BINS) + _digits(rng, 12)
    total = 0
    for index, ch in enumerate(reversed(body)):
        digit = int(ch) * 2 if index % 2 == 0 else int(ch)
        total += digit - 9 if digit > 9 else digit
    return body + str((10 - total % 10) % 10)


def _bank_account(rng: random.Random) -> str:
    return rng.choice(("62", "60")) + _digits(rng, 17)


def _credit_code(rng: random.Random) -> str:
    """GB 32100 校验位正确的 18 位统一社会信用代码。"""
    charset = "0123456789ABCDEFGHJKLMNPQRTUWXY"
    weights = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
    body = rng.choice(("91", "93")) + rng.choice(ID_REGIONS)[:2] + "".join(rng.choice(charset) for _ in range(9))
    return body + charset[(31 - sum(charset.index(c) * w for c, w in zip(body, weights)) % 31) % 31]


def _org_name(rng: random.Random) -> str:
    return rng.choice(tuple(CITY_GEO)) + rng.choice(BRANDS) + rng.choice(INDUSTRIES) + rng.choice(ORG_FORMS)


def _bank_branch(rng: random.Random) -> str:
    city = rng.choice(tuple(CITY_GEO))
    districts, _streets = CITY_GEO[city]
    return rng.choice(BANKS) + "股份有限公司" + city[:-1] + rng.choice(districts) + "支行"


def _date_cn(rng: random.Random) -> str:
    return f"{rng.randrange(2025, 2027)}年{rng.randrange(1, 13)}月{rng.randrange(1, 29)}日"


def _amount_cn(rng: random.Random) -> tuple[str, str]:
    """返回 (人民币大写, 数字写法)；粒度为仟元，保证大写转换正确。"""
    digits = "零壹贰叁肆伍陆柒捌玖"
    wan, qian = rng.randrange(8, 960), rng.choice((0, 0, 5, 8))
    group_text = ""
    for divisor, unit in ((1000, "仟"), (100, "佰"), (10, "拾"), (1, "")):
        digit = (wan // divisor) % 10
        if digit:
            group_text += digits[digit] + unit
        elif group_text and divisor > 1 and wan % divisor:
            group_text += "零"
    qian_text = (digits[qian] + "仟") if qian else ""
    return f"人民币{group_text}万{qian_text}元整", f"¥{(wan * 10 + qian) * 1000:,}"


def _contract_no(rng: random.Random) -> str:
    return f"{rng.choice(('HT', 'XY', 'JK'))}-{rng.randrange(2024, 2027)}-{rng.randrange(10, 99):02d}{rng.randrange(10, 99):02d}-{rng.randrange(1, 999):03d}"


# ── 文档装配 ───────────────────────────────────────────────────

def _meta(b: _DocBuilder, label: str, value: str, entity_type: str | None) -> None:
    """写一行 '标签 + 值'；entity_type 非空时把值登记为植入实体。"""
    b.write(label)
    if entity_type is None:
        b.write(f"{value}\n")
        return
    b.write_entity(value, entity_type)
    b.write("\n")


def _org_party_block(b: _DocBuilder, rng: random.Random, role: str, org: str) -> None:
    person = _person_name(rng)
    _meta(b, f"{role}：", org, "INSTITUTION_NAME")
    _meta(b, "统一社会信用代码：", _credit_code(rng), "CREDIT_CODE")
    _meta(b, "法定代表人：", person, "PERSON")
    _meta(b, "身份证号：", _id_card(rng), "ID_CARD")
    _meta(b, "住所：", _address(rng), "ADDRESS")
    _meta(b, "联系电话：", _phone(rng), "PHONE")
    _meta(b, "电子邮箱：", _email(rng, person), "EMAIL")
    _meta(b, "开户银行：", _bank_branch(rng), "BANK_NAME")
    _meta(b, "银行账号：", _bank_account(rng), "BANK_ACCOUNT")
    _meta(b, "银行卡号：", _bank_card(rng), "BANK_CARD")


def _foreign_party_block(b: _DocBuilder, rng: random.Random, role: str) -> None:
    person = _person_name(rng)
    _meta(b, f"{role}：", person, "PERSON")
    _meta(b, "护照号码：", _passport(rng), "PASSPORT")
    _meta(b, "住址：", _address(rng), "ADDRESS")
    _meta(b, "联系电话：", _phone(rng), "PHONE")
    _meta(b, "电子邮箱：", _email(rng, person), "EMAIL")


def _signature_block(b: _DocBuilder, role: str, org: str | None, person: str, signing_date: str) -> None:
    if org is not None:
        _meta(b, f"{role}（盖章）：", org, "INSTITUTION_NAME")
    else:
        _meta(b, f"{role}（签字）：", person, "PERSON")
    _meta(b, "法定代表人或授权代表（签字）：", person, "PERSON")
    _meta(b, "签署日期：", signing_date, "DATE")


def _amount_clause(b: _DocBuilder, rng: random.Random) -> None:
    upper, digital = _amount_cn(rng)
    b.write("本合同总金额为")
    b.write_entity(upper, "AMOUNT")
    b.write("（")
    b.write_entity(digital, "AMOUNT")
    b.write("）。支付节点、结算方式与发票开具要求，由双方在附件中另行列明。\n")


def _appendix_account(b: _DocBuilder, rng: random.Random, org: str) -> None:
    """附件：收款与账户信息（密集实体区，模拟真实合同的落款附件）。"""
    person = _person_name(rng)
    b.write("附件一：收款账户与联络信息\n")
    _meta(b, "户名：", org, "INSTITUTION_NAME")
    _meta(b, "开户银行：", _bank_branch(rng), "BANK_NAME")
    _meta(b, "银行账号：", _bank_account(rng), "BANK_ACCOUNT")
    _meta(b, "银行卡号：", _bank_card(rng), "BANK_CARD")
    _meta(b, "财务联系人：", person, "PERSON")
    _meta(b, "联系人电话：", _phone(rng), "PHONE")
    _meta(b, "联系人邮箱：", _email(rng, person), "EMAIL")


def _pad_clauses(b: _DocBuilder, target_chars: int) -> None:
    """用长条款填充到目标规模；条款按序号编号保证行唯一。"""
    index = 1
    while b.length < target_chars - 250:
        b.write(f"第{index}条　{_FILLER_CLAUSES[(index - 1) % len(_FILLER_CLAUSES)]}\n")
        index += 1


def build_doc(spec: dict) -> tuple[str, list[dict]]:
    rng = random.Random(SEED_BASE + spec["seed_offset"])
    b = _DocBuilder()
    density = spec["density"]

    org_a, org_b = _org_name(rng), _org_name(rng)
    person_a, person_b = _person_name(rng), _person_name(rng)

    # 标题与合同元信息
    b.write_entity(org_a, "INSTITUTION_NAME")
    b.write("与")
    b.write_entity(org_b, "INSTITUTION_NAME")
    b.write(f"{rng.choice(CONTRACT_KINDS)}\n\n")
    _meta(b, "合同编号：", _contract_no(rng), "CASE_NUMBER")
    _meta(b, "签订日期：", _date_cn(rng), "DATE")
    _meta(b, "生效日期：", _date_cn(rng), "DATE")
    b.write("\n")

    # 当事方
    b.write("第一条　当事方\n")
    if density == "dense":
        b.write("甲方（委托方）与乙方（服务方）基本信息如下，丙方以外籍技术顾问身份参与本项目：\n")
    elif density == "medium":
        b.write("甲方（委托方）与乙方（服务方）基本信息如下，丙方为乙方聘请的外籍技术顾问：\n")
    else:
        b.write("甲方（委托方）基本信息如下，乙方为外资公司，其授权代表信息一并列明：\n")
    _org_party_block(b, rng, "甲方", org_a)
    b.write("\n")
    _org_party_block(b, rng, "乙方", org_b)
    b.write("\n")
    _foreign_party_block(b, rng, "丙方" if density != "sparse" else "乙方授权代表")
    b.write("\n")

    # 金额条款（不同密度条数不同）
    b.write("第二条　价款与结算\n")
    _amount_clause(b, rng)
    if density != "sparse":
        _amount_clause(b, rng)
    b.write("\n")

    # 填充条款（决定文档规模）
    _pad_clauses(b, spec["target_chars"])

    # 签署页（重复植入当事方关键实体，模拟真实合同落款）
    b.write("\n签署页\n")
    _signature_block(b, "甲方", org_a, person_a, _date_cn(rng))
    b.write("\n")
    _signature_block(b, "乙方", org_b, person_b, _date_cn(rng))
    if density == "dense":
        b.write("\n")
        _appendix_account(b, rng, org_a)

    return b.render(), b.entities


# ── 覆盖校验与输出 ─────────────────────────────────────────────

def read_default_generic_types() -> list[str]:
    """读取 entity_type_service.get_default_generic_types 的同源数据并过滤。"""
    with open(PRESET_JSON, encoding="utf-8") as handle:
        raw = json.load(handle)
    return [
        type_id
        for type_id, item in raw.items()
        if item.get("enabled", True) and item.get("default_enabled") and not type_id.lower().startswith("custom_")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成长文档 NER 基准语料（合同风格中文 txt + manifest）")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="输出目录（默认 scripts/eval/artifacts/corpus）")
    args = parser.parse_args()

    default_ids = read_default_generic_types()
    print(f"[gen] 默认启用通用类型（{len(default_ids)}）：{', '.join(default_ids)}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 幂等：只清理本脚本产出的文件（doc_*.txt 与 manifest.json），不动其他文件
    for stale in out_dir.glob("doc_*.txt"):
        stale.unlink()
    stale_manifest = out_dir / "manifest.json"
    if stale_manifest.exists():
        stale_manifest.unlink()

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed_base": SEED_BASE,
        "generator": "gen_ner_contract_corpus.py",
        "default_generic_types": default_ids,
        "offset_convention": "start/end 为 txt 文件 utf-8 解码后的 Python 字符下标；后端 content 与文件逐字一致",
        "docs": [],
    }

    for spec in DOC_SPECS:
        text, entities = build_doc(spec)
        for entity in entities:
            entity["in_default_schema"] = entity["type"] in default_ids
        covered = {entity["type"] for entity in entities if entity["in_default_schema"]}
        missing = [type_id for type_id in default_ids if type_id not in covered]
        if missing:
            raise SystemExit(f"[gen] {spec['doc']} 未覆盖默认启用类型：{', '.join(missing)}")
        # offset 自校验：按 manifest 下标回取的片段必须与实体文本一致
        for entity in entities:
            assert text[entity["start"]:entity["end"]] == entity["text"], (
                f"{spec['doc']} 实体 offset 校验失败：{entity['text']}"
            )

        txt_path = out_dir / f"{spec['doc']}.txt"
        txt_path.write_text(text, encoding="utf-8")
        by_type: dict[str, int] = {}
        for entity in entities:
            by_type[entity["type"]] = by_type.get(entity["type"], 0) + 1
        manifest["docs"].append({
            "doc": spec["doc"],
            "file": txt_path.name,
            "density": spec["density"],
            "seed": SEED_BASE + spec["seed_offset"],
            "chars": len(text),
            "bytes": txt_path.stat().st_size,
            "entity_total": len(entities),
            "entity_count_by_type": by_type,
            "entities": entities,
        })
        in_schema = sum(1 for entity in entities if entity["in_default_schema"])
        print(
            f"[gen] {spec['doc']}: {len(text)} 字符（密度 {spec['density']}）"
            f" | 植入实体 {len(entities)} 个（默认 schema 内 {in_schema}）"
            f" | 覆盖 {len(covered)}/{len(default_ids)}"
        )

    with open(stale_manifest, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(f"[gen] manifest -> {stale_manifest}")


if __name__ == "__main__":
    main()
