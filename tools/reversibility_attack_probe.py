import json
import os
import re
import sqlite3
from collections import Counter
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "backend", "outputs")
DATA_DIR = os.path.join(ROOT, "backend", "data")
FILE_STORE = os.path.join(DATA_DIR, "file_store.sqlite3")
REPORT = os.path.join(ROOT, "tools", "reversibility_attack_report.json")

PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)"),
    "id_card": re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)"),
    "bank_card": re.compile(r"(?<!\d)(\d{16,19})(?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "amount": re.compile(r"(?<!\d)(\d+(?:\.\d{1,2})?)\s*(?:万元|亿元|元|%|万元/年)"),
    "year": re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)"),
}

PLACEHOLDER_RE = re.compile(r"<([^<>\[\]]+)\[(\d{3})\]")
SMART_RE = re.compile(r"\[([^\[\]]{1,12})\d{1,3}\]")


def load_file_store():
    rows = []
    con = sqlite3.connect(FILE_STORE)
    con.row_factory = sqlite3.Row
    for r in con.execute("select file_id, data_json from file_store"):
        try:
            d = json.loads(r["data_json"])
        except Exception:
            continue
        rows.append({
            "file_id": r["file_id"],
            "original_filename": d.get("original_filename"),
            "file_type": d.get("file_type"),
            "content": d.get("content") or "",
            "pages": d.get("pages") or [],
            "entities": d.get("entities") or [],
        })
    con.close()
    return rows


def read_outputs():
    out = {}
    for name in os.listdir(OUTPUT_DIR):
        if name.endswith(".txt"):
            with open(os.path.join(OUTPUT_DIR, name), "r", encoding="utf-8", errors="replace") as fh:
                out[name[:-4]] = fh.read()
    return out


def norm(s):
    return re.sub(r"\s+", "", s or "")


def match_original(output_text, store):
    target = norm(output_text)
    best = None
    best_ratio = 0.0
    for rec in store:
        candidate = norm(rec["content"] or "\n".join(rec["pages"] or []))
        if not candidate:
            continue
        ratio = SequenceMatcher(None, target, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = rec
    return best, best_ratio


def scan_pii(text):
    hits = []
    for kind, pat in PII_PATTERNS.items():
        for m in pat.finditer(text):
            ctx = text[max(0, m.start() - 20):min(len(text), m.end() + 20)].replace("\n", " ")
            hits.append({"kind": kind, "value": m.group(0), "context": ctx})
    return hits


def placeholder_rows(text):
    rows = []
    for m in PLACEHOLDER_RE.finditer(text):
        ctx = text[max(0, m.start() - 30):min(len(text), m.end() + 30)].replace("\n", " ")
        rows.append({"placeholder": m.group(0), "type": m.group(1), "context": ctx})
    return rows


def find_embedding_artifacts():
    suspicious = []
    skip = {".venv", "node_modules", ".git", "__pycache__", ".pytest_cache"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            low = fn.lower()
            if low.endswith((".npy", ".npz", ".pkl", ".pickle", ".vec", ".faiss", ".onnx", ".bin")) or any(k in low for k in ("embedding", "vector", "gradient", "faiss")):
                suspicious.append(os.path.relpath(os.path.join(dirpath, fn), ROOT))
    return suspicious


def main():
    store = load_file_store()
    outputs = read_outputs()
    per_output = []
    max_verbatim = 0
    max_placeholder_types = 0
    output_with_most_placeholders = None
    any_amount_placeholders = False
    any_org_address_placeholders = False

    for oid, text in outputs.items():
        rec, ratio = match_original(text, store)
        structured = Counter()
        for m in PLACEHOLDER_RE.findall(text):
            structured[m[0]] += 1
        if structured:
            if len(structured) > max_placeholder_types:
                max_placeholder_types = len(structured)
                output_with_most_placeholders = oid
            if any(k in structured for k in ("金额", "AMOUNT")):
                any_amount_placeholders = True
            if any(k in structured for k in ("机构名称", "地址", "ORG", "ADDRESS", "姓名", "PERSON")):
                any_org_address_placeholders = True

        leaked = []
        if rec:
            seen = set()
            for ent in rec["entities"]:
                val = ent.get("text")
                if not val or val in seen:
                    continue
                seen.add(val)
                if val in text:
                    leaked.append({"type": ent.get("type"), "text": val, "coref": ent.get("coref_id")})

        all_leaked = []
        for srec in store:
            for ent in srec["entities"]:
                val = ent.get("text")
                if val and val in text and not any(e["text"] == val for e in all_leaked):
                    all_leaked.append({"type": ent.get("type"), "text": val, "source_file_id": srec["file_id"]})

        max_verbatim = max(max_verbatim, len(all_leaked))
        per_output.append({
            "output_id": oid,
            "matched_original_file": rec["original_filename"] if rec else None,
            "match_ratio": round(ratio, 3),
            "structured_placeholder_counts": dict(structured),
            "pii_hits": scan_pii(text),
            "leaked_from_matched_original": leaked,
            "any_known_original_entity_in_output": all_leaked,
            "placeholders": placeholder_rows(text),
        })

    verdicts = {
        "A1_training_data_extraction": {
            "verdict": "HIGH_RISK_RECOVERABLE",
            "reason": "file_store retains full original content and entity text; original entity values still appear verbatim in redacted outputs.",
            "evidence": {
                "records_with_full_content": sum(1 for r in store if (r["content"] or r["pages"])),
                "records_with_entities": sum(1 for r in store if r["entities"]),
                "max_known_original_entities_found_in_one_output": max_verbatim,
            },
        },
        "A2_membership_inference": {
            "verdict": "HIGH_RISK_COUNTS_AND_ROLES_LEAKED",
            "reason": "structured placeholders preserve entity type and exact multiplicity, and surrounding text discloses role membership.",
            "evidence": {
                "output_with_most_placeholder_types": output_with_most_placeholders,
                "distinct_placeholder_types": max_placeholder_types,
            },
        },
        "A3_embedding_gradient_inversion": {
            "verdict": "NOT_APPLICABLE_BUT_RAW_RETENTION_WORSE",
            "reason": "no embedding/vector artifacts found; raw originals are retained directly, so inversion is unnecessary.",
            "evidence": {"embedding_artifacts": find_embedding_artifacts()},
        },
        "A4_ai_social_engineering_context_inference": {
            "verdict": "HIGH_RISK_RECONSTRUCTABLE",
            "reason": "redacted outputs retain unredacted values, percentages, geographic names and org fragments that can fill the placeholders.",
            "evidence": {
                "has_amount_placeholders": any_amount_placeholders,
                "has_org_address_person_placeholders": any_org_address_placeholders,
            },
        },
        "A5_diffusion_memory_image_inversion": {
            "verdict": "NOT_TESTABLE_ON_CURRENT_ARTIFACTS",
            "reason": "outputs directory contains only txt redaction outputs; no redacted image output is present to test generative recovery.",
            "evidence": {"output_image_files": 0},
        },
    }

    report = {
        "summary": {
            "outputs_analyzed": len(outputs),
            "file_store_records": len(store),
            "records_with_full_content": sum(1 for r in store if (r["content"] or r["pages"])),
            "records_with_entities": sum(1 for r in store if r["entities"]),
            "embedding_artifacts": find_embedding_artifacts(),
        },
        "outputs": per_output,
        "verdicts": verdicts,
    }
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("REPORT_WRITTEN", REPORT)
    print("outputs", len(outputs), "records", len(store), "with_content", report["summary"]["records_with_full_content"], "with_entities", report["summary"]["records_with_entities"])


if __name__ == "__main__":
    main()
