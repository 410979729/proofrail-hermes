from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from proofrail import build_runtime_hooks as _build_runtime_hooks
from proofrail.models import PluginSettings
from proofrail.session_state import STATE_STORE


def build_runtime_hooks(*args, settings: PluginSettings | None = None, **kwargs):
    strict_settings = (
        PluginSettings(enforcement_mode="strict", validation_policy="after_each_mutation")
        if settings is None
        else replace(settings, enforcement_mode="strict", validation_policy="after_each_mutation")
    )
    return _build_runtime_hooks(*args, settings=strict_settings, **kwargs)


@pytest.fixture(autouse=True)
def _isolate_phase5_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    STATE_STORE.clear("phase5.validate-template")
    STATE_STORE.clear("phase5.validate-lifecycle")


def _record_pending_verification(hooks, session_id: str, path: str) -> None:
    hooks.post_tool_call("read_file", {"path": path}, "old\n", session_id=session_id)
    hooks.post_tool_call(
        "write_file",
        {"path": path, "content": "new\n"},
        {"success": True},
        session_id=session_id,
    )


def test_validate_only_panel_uses_shared_template_and_mode_specific_handoff(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("old\n")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "phase5.validate-template"

    _record_pending_verification(hooks, session_id, "module.py")

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "This is a verification handoff: prove the last change landed before any more mutation." in context
    assert "Complete this subtask to reopen forward progress." in context
    assert "This mode is a collaboration handoff, not a failure state." in context
    assert "current target: module.py" in context
    assert "smallest next action:" in context
    assert "Choose the first smallest next action if the next move is unclear." in context


def test_validate_only_entry_and_clear_are_audited_as_mode_lifecycle(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    target = tmp_path / "module.py"
    target.write_text("old\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(audit_log_path=str(audit_path)),
        root_dir=str(tmp_path),
    )
    session_id = "phase5.validate-lifecycle"

    _record_pending_verification(hooks, session_id, "module.py")
    hooks.post_tool_call("read_file", {"path": "module.py"}, "new\n", session_id=session_id)

    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    transitions = [entry for entry in entries if entry.get("event") == "forced_mode_transition"]

    assert len(transitions) >= 2
    entered = transitions[-2]
    cleared = transitions[-1]

    assert entered["previous_mode"] == "none"
    assert entered["mode"] == "validate_only"
    assert entered["target"] == "module.py"
    assert entered["reason"] == "pending_verification"
    assert entered["source"] == "tool_observation"

    assert cleared["previous_mode"] == "validate_only"
    assert cleared["mode"] == "none"
    assert cleared["target"] == "module.py"
    assert cleared["reason"] == "validation_complete"
    assert cleared["source"] == "tool_observation"
    assert cleared["cleared"] is True
