"""Tests for the deterministic identity-number regex floor (2026-09-21).

The floor guarantees recall for well-formed identity numbers when the closed
loop runs a single detector round (external runtime default).  These tests pin
the lineage guards (the only golden entities that previously debuted in round
>= 2), the union semantics, the CJK-safe lookarounds, and the adversarial
false-positive cases.
"""

import unittest

from app.models.schemas import Entity
from app.services.identity_regex_floor_20260921 import (
    FLOOR_PATTERNS,
    apply_identity_regex_floor,
    retype_labeled_bank_accounts,
)


def _types(*type_ids: str):
    """Build minimal type configs (only ``id`` is consumed by the floor)."""
    return [type("_T", (), {"id": type_id})() for type_id in type_ids]


def _entity(type_id: str, text: str, start: int, *, source: str = "llm") -> Entity:
    return Entity(
        id="existing",
        text=text,
        type=type_id,
        start=start,
        end=start + len(text),
        page=1,
        confidence=0.9,
        source=source,
    )


def _floor_only(text: str, type_ids, existing=None):
    result = apply_identity_regex_floor(text, _types(*type_ids), existing or [])
    return [entity for entity in result if entity.id.startswith("regex_floor_")]


class LineageGuardTests(unittest.TestCase):
    """The three golden entities that only appeared in round >= 2."""

    def test_legal_bank_card_first_seen_in_round_two(self):
        text = "账户6217001234509876543"
        floor = _floor_only(text, ["BANK_CARD"])
        self.assertEqual(len(floor), 1)
        self.assertEqual(floor[0].text, "6217001234509876543")
        self.assertEqual(floor[0].type, "BANK_CARD")
        self.assertEqual((floor[0].start, floor[0].end), (2, 21))

    def test_medical_record_id_first_seen_in_round_two(self):
        text = "病案号BA20260067890"
        floor = _floor_only(text, ["MED_RECORD_ID"])
        self.assertEqual(len(floor), 1)
        self.assertEqual(floor[0].text, "BA20260067890")
        self.assertEqual(floor[0].type, "MED_RECORD_ID")
        self.assertEqual((floor[0].start, floor[0].end), (3, 16))

    def test_inpatient_no_first_seen_in_round_two(self):
        text = "住院号ZY2026008812"
        floor = _floor_only(text, ["INPATIENT_NO"])
        self.assertEqual(len(floor), 1)
        self.assertEqual(floor[0].text, "ZY2026008812")
        self.assertEqual(floor[0].type, "INPATIENT_NO")


class CjkAdjacencyTests(unittest.TestCase):
    def test_cjk_adjacency_defeats_word_boundary_but_not_lookarounds(self):
        # Python 3 \w includes CJK, so \b would NOT fire between 号 and 6.
        text = "账号6217001234509876543，"
        floor = _floor_only(text, ["BANK_CARD"])
        self.assertEqual([entity.text for entity in floor], ["6217001234509876543"])
        self.assertEqual((floor[0].start, floor[0].end), (2, 21))


class UnionSemanticsTests(unittest.TestCase):
    def test_existing_entity_span_is_never_re_claimed_or_retyped(self):
        text = "账户6217001234509876543"
        existing = [_entity("BANK_ACCOUNT", "6217001234509876543", 2, source="has")]
        result = apply_identity_regex_floor(text, _types("BANK_CARD"), existing)
        # input list untouched, no floor entity on the covered span
        self.assertEqual(result, existing)
        self.assertEqual(len(existing), 1)
        self.assertEqual(existing[0].type, "BANK_ACCOUNT")

    def test_partial_overlap_still_suppresses_floor_entity(self):
        text = "卡号6222021234567890123"
        existing = [_entity("BANK_CARD", "622202123456789", 3)]  # 15-digit prefix
        floor = _floor_only(text, ["BANK_CARD"], existing)
        self.assertEqual(floor, [])

    def test_non_overlapping_existing_entity_does_not_block(self):
        text = "电话13800138000 卡号6222021234567890123"
        existing = [_entity("PHONE", "13800138000", 2)]
        floor = _floor_only(text, ["BANK_CARD"], existing)
        self.assertEqual([entity.text for entity in floor], ["6222021234567890123"])


class PriorityAndClaimedSpanTests(unittest.TestCase):
    def test_eighteen_digit_id_is_never_retyped_as_bank_card_or_phone(self):
        text = "身份证13800138000123456X"
        floor = _floor_only(text, ["ID_CARD", "BANK_CARD", "PHONE"])
        self.assertEqual([entity.type for entity in floor], ["ID_CARD"])
        self.assertEqual(floor[0].text, "13800138000123456X")

    def test_phone_substring_inside_longer_digit_run_is_not_a_phone(self):
        text = "联系电话1380013800099"
        floor = _floor_only(text, ["PHONE"])
        self.assertEqual(floor, [])

    def test_registry_priority_order_assigns_floor_ids(self):
        # BANK_CARD appears earlier in the text, but ID_CARD has priority.
        text = "卡号6222021234567890123，身份证11010119900307891X。"
        floor = _floor_only(text, ["BANK_CARD", "ID_CARD"])
        self.assertEqual([entity.type for entity in floor], ["ID_CARD", "BANK_CARD"])
        self.assertEqual([entity.id for entity in floor], ["regex_floor_0001", "regex_floor_0002"])


class RequestedTypeFilterTests(unittest.TestCase):
    def test_type_not_requested_never_matches(self):
        text = "卡号6222021234567890123"
        self.assertEqual(_floor_only(text, ["PHONE"]), [])
        self.assertEqual(_floor_only(text, []), [])

    def test_type_id_matching_is_case_insensitive(self):
        text = "卡号6222021234567890123"
        floor = _floor_only(text, ["bank_card"])
        self.assertEqual([entity.text for entity in floor], ["6222021234567890123"])
        self.assertEqual(floor[0].type, "BANK_CARD")  # emitted canonical id

    def test_domain_codes_share_shape_but_respect_priority(self):
        text = "病案号BA20260067890"
        both = _floor_only(text, ["MED_RECORD_ID", "INPATIENT_NO"])
        self.assertEqual([entity.type for entity in both], ["MED_RECORD_ID"])
        only_inpatient = _floor_only(text, ["INPATIENT_NO"])
        self.assertEqual([entity.type for entity in only_inpatient], ["INPATIENT_NO"])


class AdversarialFalsePositiveTests(unittest.TestCase):
    ALL = ["ID_CARD", "BANK_CARD", "PASSPORT", "PHONE", "EMAIL", "MED_RECORD_ID", "INPATIENT_NO"]

    def test_date_like_run_does_not_match(self):
        self.assertEqual(_floor_only("日期20260921。", self.ALL), [])

    def test_fifteen_and_twenty_digit_runs_do_not_match(self):
        self.assertEqual(_floor_only("编号123456789012345", self.ALL), [])
        self.assertEqual(_floor_only("编号12345678901234567890", self.ALL), [])

    def test_non_passport_letter_prefix_does_not_match(self):
        self.assertEqual(_floor_only("证件A12345678", self.ALL), [])

    def test_phone_inside_eighteen_char_id_does_not_match(self):
        text = "身份证13800138000123456X"
        self.assertEqual(_floor_only(text, ["PHONE"]), [])

    def test_email_inside_longer_token_does_not_match(self):
        # The trailing non-word guard rejects the word-char-glued token;
        # \b could not (the CJK/underscore are word chars in Python 3).
        self.assertEqual(_floor_only("编号user@example.com_x1", ["EMAIL"]), [])

    def test_practice_certificate_number_is_not_a_bank_card(self):
        # ISO/IEC 7812 MII: no card network issues cards starting with 1.
        # A lawyer's 17-digit practice-certificate number must not be
        # claimed as BANK_CARD (corpus FP found by the FP scan).
        self.assertEqual(_floor_only("执业证号：13301201010223344", ["BANK_CARD"]), [])


class ConventionTests(unittest.TestCase):
    def test_floor_entity_conventions(self):
        text = "账户6217001234509876543"
        floor = _floor_only(text, ["BANK_CARD"])
        entity = floor[0]
        self.assertTrue(entity.id.startswith("regex_floor_"))
        self.assertEqual(entity.source, "regex")
        self.assertIsNone(entity.confidence)
        self.assertIsNone(entity.coref_id)
        self.assertEqual(entity.page, 1)

    def test_true_positive_email_and_leading_guard(self):
        text = "邮箱 zhangsan@example.com。"
        floor = _floor_only(text, ["EMAIL"])
        self.assertEqual([entity.text for entity in floor], ["zhangsan@example.com"])
        guarded = _floor_only("(zhangsan@example.com)", ["EMAIL"])
        self.assertEqual([entity.text for entity in guarded], ["zhangsan@example.com"])


class RegistryTests(unittest.TestCase):
    def test_registry_is_ordered_and_compiled_once(self):
        self.assertEqual(
            list(FLOOR_PATTERNS),
            ["ID_CARD", "BANK_CARD", "PASSPORT", "PHONE", "EMAIL", "MED_RECORD_ID", "INPATIENT_NO"],
        )
        for pattern in FLOOR_PATTERNS.values():
            self.assertIsInstance(pattern.pattern, str)
            self.assertNotIn(r"\b", pattern.pattern)


class BankAccountLabelRetypeTests(unittest.TestCase):
    """BANK_CARD vs BANK_ACCOUNT: same format, decided by the label."""

    CARD = "6222021234567890123"

    def _card_entity(self, start: int, *, source: str = "has") -> Entity:
        return Entity(
            id="card",
            text=self.CARD,
            type="BANK_CARD",
            start=start,
            end=start + len(self.CARD),
            page=1,
            confidence=0.9,
            source=source,
        )

    def test_account_label_retypes_to_bank_account(self):
        text = f"转入账户 {self.CARD}"
        result = retype_labeled_bank_accounts([self._card_entity(4)], text, _types("BANK_ACCOUNT", "BANK_CARD"))
        self.assertEqual([entity.type for entity in result], ["BANK_ACCOUNT"])

    def test_card_label_keeps_bank_card(self):
        text = f"卡号{self.CARD}"
        result = retype_labeled_bank_accounts([self._card_entity(2)], text, _types("BANK_ACCOUNT", "BANK_CARD"))
        self.assertEqual([entity.type for entity in result], ["BANK_CARD"])

    def test_card_between_label_and_span_keeps_bank_card(self):
        text = f"账户卡号{self.CARD}"
        result = retype_labeled_bank_accounts([self._card_entity(4)], text, _types("BANK_ACCOUNT", "BANK_CARD"))
        self.assertEqual([entity.type for entity in result], ["BANK_CARD"])

    def test_not_requested_never_retypes(self):
        text = f"转入账户 {self.CARD}"
        result = retype_labeled_bank_accounts([self._card_entity(4)], text, _types("BANK_CARD"))
        self.assertEqual([entity.type for entity in result], ["BANK_CARD"])

    def test_legal_identity_card_without_account_type_stays_bank_card(self):
        # The legal golden identity entity sits behind an 账户 label; legal
        # does not request BANK_ACCOUNT, so the identity gate is untouched.
        text = "账户6217001234509876543"
        entity = Entity(
            id="legal",
            text="6217001234509876543",
            type="BANK_CARD",
            start=2,
            end=21,
            page=1,
            confidence=None,
            source="regex",
        )
        result = retype_labeled_bank_accounts([entity], text, _types("BANK_CARD"))
        self.assertEqual([entity.type for entity in result], ["BANK_CARD"])

    def test_floor_sourced_entity_is_also_retyped(self):
        text = f"户名{self.CARD}"
        entity = Entity(
            id="regex_floor_0001",
            text=self.CARD,
            type="BANK_CARD",
            start=2,
            end=2 + len(self.CARD),
            page=1,
            confidence=None,
            source="regex",
        )
        result = retype_labeled_bank_accounts([entity], text, _types("BANK_ACCOUNT"))
        self.assertEqual([entity.type for entity in result], ["BANK_ACCOUNT"])

    def test_input_list_not_mutated(self):
        text = f"转入账户 {self.CARD}"
        entities = [self._card_entity(4)]
        retype_labeled_bank_accounts(entities, text, _types("BANK_ACCOUNT"))
        self.assertEqual(entities[0].type, "BANK_CARD")


if __name__ == "__main__":
    unittest.main()
