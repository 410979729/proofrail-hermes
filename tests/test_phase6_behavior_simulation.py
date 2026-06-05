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
def _isolate_phase6_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for session_id in (
        "phase6.block-lifecycle",
        "phase6.forward-progress",
    ):
        STATE_STORE.clear(session_id)


def test_missing_evidence_and_low_signal_blocks_are_audited_as_mode_transitions(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    target = tmp_path / "module.py"
    target.write_text("old\n")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    hooks.audit.path = audit_path
    session_id = "phase6.block-lifecycle"

    blocked = hooks.pre_tool_call(
        "write_file",
        {"path": "module.py", "content": "new\n"},
        session_id=session_id,
    )
    assert blocked is not None

    hooks.post_tool_call("read_file", {"path": "module.py"}, "old\n", session_id=session_id)
    hooks.post_tool_call("read_file", {"path": "module.py"}, "old\n", session_id=session_id)
    hooks.post_tool_call("search_files", {"pattern": "nomatch", "path": str(tmp_path)}, "", session_id=session_id)
    hooks.post_tool_call("search_files", {"pattern": "nomatch", "path": str(tmp_path)}, "", session_id=session_id)

    low_signal = hooks.pre_tool_call(
        "search_files",
        {"pattern": "nomatch", "path": str(tmp_path)},
        session_id=session_id,
    )
    assert low_signal is not None
    assert "Smallest next action:" in low_signal["message"]
    assert "Avoid right now:" in low_signal["message"]

    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    transitions = [entry for entry in entries if entry.get("event") == "forced_mode_transition"]

    assert any(
        entry.get("mode") == "gather_target_evidence"
        and entry.get("reason") == "missing_evidence"
        and entry.get("source") == "tool_block"
        and entry.get("previous_mode") == "none"
        and entry.get("cleared") is False
        for entry in transitions
    )
    assert any(
        entry.get("mode") == "change_strategy"
        and entry.get("reason") == "low_signal_repeat"
        and entry.get("source") == "tool_block"
        and entry.get("previous_mode") == "none"
        and entry.get("cleared") is False
        for entry in transitions
    )



def test_end_to_end_behavior_emits_forward_progress_reopened_signal(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    target = tmp_path / "module.py"
    target.write_text("old\n")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    hooks.audit.path = audit_path
    session_id = "phase6.forward-progress"

    blocked = hooks.pre_tool_call(
        "write_file",
        {"path": "module.py", "content": "new\n"},
        session_id=session_id,
    )
    assert blocked is not None
    assert "Proofrail mode switch: gather_target_evidence" in blocked["message"]

    hooks.post_tool_call("read_file", {"path": "module.py"}, "old\n", session_id=session_id)
    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "current proofrail mode: none" not in context

    assert hooks.pre_tool_call(
        "write_file",
        {"path": "module.py", "content": "new\n"},
        session_id=session_id,
    ) is None
    hooks.post_tool_call(
        "write_file",
        {"path": "module.py", "content": "new\n"},
        {"success": True},
        session_id=session_id,
    )

    validate_block = hooks.pre_tool_call(
        "search_files",
        {"pattern": "module", "path": str(tmp_path)},
        session_id=session_id,
    )
    assert validate_block is not None
    assert "Proofrail mode switch: validate_only" in validate_block["message"]
    assert "Smallest next action:" in validate_block["message"]

    hooks.post_tool_call("read_file", {"path": "module.py"}, "new\n", session_id=session_id)

    state = hooks.explain_state(session_id)
    assert state["forced_next_mode"] == "none"
    assert state["pending_verification"] is False

    compact = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "validation complete" in compact
    assert "forward progress reopened" in compact

    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    reopened = [entry for entry in entries if entry.get("event") == "forward_progress_reopened"]
    assert reopened
    latest = reopened[-1]
    assert latest["session_id"] == session_id
    assert latest["trigger"] == "validation_complete"
    assert latest["target"] == "module.py"
    assert latest["from_mode"] == "validate_only"
