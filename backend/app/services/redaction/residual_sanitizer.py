"""Residual sensitive-value sanitizer used to close table/header/footer leaks.

The NER pipeline only replaces detected entities. Text that was missed by the
model (or left untouched in tables, headers, and footers) is caught here by a
conservative set of patterns and replaced with the same privacy label used by
the hardened replacement strategy.
"""
from __future__ import annotations

import re
from typing import Any

PRIVACY_LABEL = "[已匿名]"

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
_BANK_CARD_RE = re.compile(r"(?<!\d)(\d{16,19})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_AMOUNT_RE = re.compile(r"(?<!\d)(\d+(?:\.\d{1,2})?)(?:\s*)(?:万元|亿元|元|万元/年)")
_PERCENT_RE = re.compile(r"(?<!\d)(\d+(?:\.\d{1,2})?)%")

_GEO_TERMS = [
    "寿蜀产业园智慧能源虚拟电厂项目",
    "寿县蜀山现代产业园",
    "寿蜀产业园",
    "合淮同城化先行区",
    "新桥空港经济核心区",
    "长三角产业转移重要承载区",
    "合肥都市圈",
    "新桥国际机场",
    "市长江西路",
    "淮南市",
    "合肥",
    "六安",
    "寿县",
    "淮南",
    "安徽",
    "长三角",
    "合淮",
    "新桥",
    "蜀山",
]

_PATTERNS: list[tuple[str, Any]] = [
    ("phone", _PHONE_RE),
    ("id_card", _ID_CARD_RE),
    ("bank_card", _BANK_CARD_RE),
    ("email", _EMAIL_RE),
    ("amount", _AMOUNT_RE),
    ("percent", _PERCENT_RE),
]


def build_residual_replacements(content: str, label: str = PRIVACY_LABEL) -> dict[str, str]:
    """Return replacements for residual sensitive values found in ``content``."""
    if not content:
        return {}
    replacements: dict[str, str] = {}
    for _kind, pattern in _PATTERNS:
        for match in pattern.finditer(content):
            value = match.group(0)
            replacements.setdefault(value, label)
    for term in _GEO_TERMS:
        if term in content:
            replacements[term] = label
    return replacements


__all__ = ["PRIVACY_LABEL", "build_residual_replacements"]
