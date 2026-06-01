from __future__ import annotations

from pathlib import Path

import pytest

from proofrail.classifier import HermesLlmGuardrailClassifier
from proofrail.session_state import STATE_STORE


class UnsupportedStructuredOutputError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    STATE_STORE.clear("classifier-fallback")
    STATE_STORE.clear("classifier-fallback-allow")


def test_llm_classifier_falls_back_to_rule_when_complete_structured_is_unsupported(tmp_path: Path) -> None:
    class FakeLlm:
        def complete_structured(self, **_kwargs):
            raise UnsupportedStructuredOutputError("400 response_format json_schema is not supported")

    target = tmp_path / "module.py"
    target.write_text("print('old')\n")

    classifier = HermesLlmGuardrailClassifier(llm=FakeLlm())
    state = STATE_STORE.get("classifier-fallback")
    state.phase = "execute"
    state.evidence_count = 1
    state.last_evidence_label = "search_files: broad query over repo"
    state.pending_verification = False

    decision = classifier(
        tool_name="write_file",
        args={"path": str(target), "content": "print('new')\n"},
        session_state=state,
        command="",
        category="write",
        is_mutation=True,
    )

    assert decision is not None
    assert decision.decision == "block"
    assert decision.evidence_gap == "target_state"
    assert decision.source == "rule_fallback"
    assert "Inspect" in decision.guidance[0]


def test_llm_classifier_fallback_returns_none_when_rule_classifier_has_no_opinion(tmp_path: Path) -> None:
    class FakeLlm:
        def complete_structured(self, **_kwargs):
            raise UnsupportedStructuredOutputError("400 json_schema unsupported")

    target = tmp_path / "new.txt"
    classifier = HermesLlmGuardrailClassifier(llm=FakeLlm())
    state = STATE_STORE.get("classifier-fallback-allow")
    state.phase = "execute"
    state.evidence_count = 1
    state.last_evidence_label = f"read_file: {target}"
    state.pending_verification = False

    decision = classifier(
        tool_name="write_file",
        args={"path": str(target), "content": "new"},
        session_state=state,
        command="",
        category="write",
        is_mutation=True,
    )

    assert decision is None
