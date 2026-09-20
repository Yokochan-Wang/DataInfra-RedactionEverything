"""Tests for the additive recognition-duration SPA overlay."""

from pathlib import Path

from app.services.recognition_duration_ui_20260902 import (
    _SCRIPT_TAG,
    _frontend_index_response,
)


def test_frontend_index_response_injects_date_versioned_duration_script(tmp_path: Path):
    index = tmp_path / "index.html"
    index.write_text("<html><body><div id='root'></div></body></html>", encoding="utf-8")

    response = _frontend_index_response(index)

    assert response.status_code == 200
    assert _SCRIPT_TAG in response.body.decode("utf-8")


def test_frontend_index_response_does_not_duplicate_script_tag(tmp_path: Path):
    index = tmp_path / "index.html"
    index.write_text(f"<html><body>{_SCRIPT_TAG}</body></html>", encoding="utf-8")

    response = _frontend_index_response(index)
    html = response.body.decode("utf-8")

    assert html.count(_SCRIPT_TAG) == 1
