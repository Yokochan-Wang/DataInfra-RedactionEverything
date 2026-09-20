"""Tests for the dated all-semantic-types-per-request overlay."""

from app.services.has_service_20260902 import _all_types_in_one_batch


class _Type:
    def __init__(self, type_id: str, name: str):
        self.id = type_id
        self.name = name


class _FakeService:
    @staticmethod
    def _convert_entity_types_to_chinese(entity_types):
        return [str(getattr(item, "name", "") or getattr(item, "id", "")) for item in entity_types]


def test_all_selected_types_are_sent_in_one_batch():
    service = _FakeService()
    types = [_Type("PERSON", "姓名"), _Type("ADDRESS", "地址"), _Type("PHONE", "电话")]

    batches = _all_types_in_one_batch(service, types)

    assert len(batches) == 1
    assert batches[0] == types


def test_duplicate_model_type_names_are_deduplicated():
    service = _FakeService()
    first = _Type("PERSON", "姓名")
    duplicate = _Type("CUSTOM_PERSON", "姓名")

    batches = _all_types_in_one_batch(service, [first, duplicate])

    assert batches == [[first]]
