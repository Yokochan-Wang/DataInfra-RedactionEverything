"""Suite-wide pytest fixtures.

Test-isolation guard for the HaS NER external-runtime gate bypass.
"""

import pytest

from app.core.config import settings

# Configured value captured before any test runs (field default True; the
# root .env does not set it). Restored before every test below.
_GATE_BYPASS_CONFIGURED = settings.HAS_NER_EXTERNAL_GATE_BYPASS


@pytest.fixture(autouse=True)
def _restore_gate_bypass_killswitch():
    """Undo the kill-switch injection leaked by the runtime-gate RED tests.

    ``test_external_runtime_killswitch_disables_bypass`` disables the bypass
    with ``object.__setattr__(settings, "HAS_NER_EXTERNAL_GATE_BYPASS",
    False)``. ``monkeypatch`` does not track ``object.__setattr__``, so the
    False value survives test teardown and leaks into every later test in the
    same process, silently re-engaging the global GPU gate for them (e.g.
    ``test_has_extract_entities_external_runtime_allows_parallel`` would
    observe a serialized peak of 1 instead of 4). Restoring the configured
    value before each test keeps one test's injection from changing another
    test's behavior; it does not alter any test's own assertions.
    """
    setattr(settings, "HAS_NER_EXTERNAL_GATE_BYPASS", _GATE_BYPASS_CONFIGURED)


@pytest.fixture(autouse=True)
def _isolate_ner_runtime_store(monkeypatch, tmp_path):
    """Keep every test off the real data/ner_backend.json.

    The text-model runtime file now outranks .env, so a leftover file — or the
    one test_role_matrix's PUT writes — would silently steer later tests at the
    wrong NER endpoint (and flip is_remote_text_runtime for the gate tests).
    """
    from app.core import ner_runtime

    monkeypatch.setattr(ner_runtime, "_path", lambda: str(tmp_path / "ner_backend.json"))
