# Copyright 2026 DataInfra-RedactionEverything Contributors
"""Regex-floor false-positive scan over the golden corpus.

Runs the deterministic identity-number floor (same registry, priority order
and claimed-span suppression as production) over every corpus document with
the pack's requested type list, then diffs the matches against the pack's
ground truth.  A match is GOLDEN when its text appears in the pack's
expected.json; anything else is reported as a potential false positive for
human adjudication.

Usage:  python scripts/eval/scan_regex_floor_fp.py
Gate:   0 unjustified non-golden matches.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
CORPUS_DIR = BACKEND_DIR.parent / "e2e" / "corpus"
sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_regex_floor_20260921 import (  # noqa: E402
    apply_identity_regex_floor,
)

DEFAULT_TYPE_IDS = [
    "PERSON", "ID_CARD", "PASSPORT", "PHONE", "EMAIL",
    "ADDRESS", "BANK_CARD", "INSTITUTION_NAME", "DATE",
]


def _types(type_ids: list[str]):
    return [type("_T", (), {"id": type_id})() for type_id in type_ids]


def _golden_texts(expected: dict) -> set[str]:
    golden: set[str] = set()
    for name, groups in expected.items():
        if name.startswith("_") or not isinstance(groups, dict):
            continue
        for entities in groups.values():
            for entity in entities or []:
                golden.add(str(entity.get("text") or ""))
    return golden


def main() -> int:
    total_by_pattern: dict[str, int] = {}
    golden_total = 0
    non_golden: list[tuple[str, str, str]] = []

    for pack_dir in sorted(p for p in CORPUS_DIR.iterdir() if p.is_dir()):
        expected = json.loads((pack_dir / "expected.json").read_text(encoding="utf-8"))
        type_ids = expected.get("_entity_type_ids") or DEFAULT_TYPE_IDS
        golden = _golden_texts(expected)
        for doc in sorted(pack_dir.glob("*.txt")):
            text = doc.read_text(encoding="utf-8")
            floor = apply_identity_regex_floor(text, _types(type_ids), [])
            for entity in floor:
                total_by_pattern[entity.type] = total_by_pattern.get(entity.type, 0) + 1
                if entity.text in golden:
                    golden_total += 1
                else:
                    non_golden.append((pack_dir.name, doc.name, f"{entity.type} {entity.text}"))

    print("== floor matches per pattern ==")
    for pattern, count in sorted(total_by_pattern.items()):
        print(f"  {pattern}: {count}")
    print(f"golden matches: {golden_total}")
    print(f"non-golden matches: {len(non_golden)}")
    for pack, doc, detail in non_golden:
        print(f"  [FP?] {pack}/{doc}: {detail}")

    if non_golden:
        print("\nFP_SCAN_FAIL: adjudicate every non-golden match above "
              "(tighten the pattern or whitelist with a reason).")
        return 1
    print("\nFP_SCAN_PASS: every floor match is golden-corpus evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
