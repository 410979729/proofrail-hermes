from __future__ import annotations

import json
from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.models import PluginSettings
from proofrail.settings import settings_from_mapping
from proofrail.summarize import summarize_large_output
from proofrail.tooling import get_tool_category


@pytest.fixture(autouse=True)
def _isolate_default_audit_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_default_guidance_settings_are_snake_case() -> None:
    settings = settings_from_mapping({})

    assert settings.enforcement_mode == "advisory"
    assert settings.advisory_injection == "compact"
    assert settings.validation_policy == "batch"
    assert settings.mutation_batch_max == 5
    assert settings.dangerous_command_action == "warn"


def test_explain_state_reports_configured_enforcement_mode() -> None:
    hooks = build_runtime_hooks(settings=PluginSettings(enforcement_mode="strict"))

    state = hooks.explain_state("configured-mode")

    assert state["enforcement_mode"] == "strict"


def test_advisory_missing_evidence_does_not_block(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    audit_path = tmp_path / "audit.jsonl"
    hooks = build_runtime_hooks(
        settings=PluginSettings(audit_log_path=str(audit_path)),
        root_dir=str(tmp_path),
    )

    decision = hooks.pre_tool_call(
        "write_file",
        {"path": "app.py", "content": "print('new')\n"},
        session_id="advisory-missing-evidence",
    )

    assert decision is None
    state = hooks.explain_state("advisory-missing-evidence")
    assert state["last_advisory"]["reason"] == "missing_evidence"
    assert state["last_advisory"]["fastest_next_action"] == "inspect app.py directly"
    context = hooks.pre_llm_call(session_id="advisory-missing-evidence")["context"]
    assert "Proofrail advisory [missing_evidence]" in context
    assert "fastest next action: inspect app.py directly" in context
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(entry.get("event") == "advisory" and entry.get("reason") == "missing_evidence" for entry in entries)


def test_full_advisory_injection_renders_active_advisory_panel(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(advisory_injection="full"),
        root_dir=str(tmp_path),
    )
    session_id = "full-advisory-panel"

    assert hooks.pre_tool_call(
        "write_file",
        {"path": "app.py", "content": "print('new')\n"},
        session_id=session_id,
    ) is None

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "## [PROOFRAIL TASK PANEL" in context
    assert "## [SYSTEM STATUS — active advisories]" in context
    assert "[missing_evidence] No target-local evidence" in context


def test_strict_missing_evidence_blocks(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="strict"),
        root_dir=str(tmp_path),
    )

    decision = hooks.pre_tool_call(
        "write_file",
        {"path": "app.py", "content": "print('new')\n"},
        session_id="strict-missing-evidence",
    )

    assert decision is not None
    assert decision["action"] == "block"
    assert "Blocked by Proofrail [missing_evidence]" in decision["message"]


def test_pending_verification_is_advisory_by_default(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "pending-verification-default-advisory"

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, session_id=session_id) is None
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, {"success": True}, session_id=session_id)

    decision = hooks.pre_tool_call(
        "write_file",
        {"path": "app.py", "content": "print('newer')\n"},
        session_id=session_id,
    )

    assert decision is None
    state = hooks.explain_state(session_id)
    assert state["pending_verification"] is True
    assert state["last_advisory"]["reason"] == "pending_verification"
    assert "validate" in state["last_advisory"]["fastest_next_action"]


def test_apply_patch_alias_is_write() -> None:
    assert get_tool_category("apply_patch") == "write"


def test_evidence_paths_prevent_unrelated_evidence_unlock(tmp_path: Path) -> None:
    observed = tmp_path / "a.py"
    target = tmp_path / "b.py"
    observed.write_text("print('a')\n")
    target.write_text("print('b')\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="strict"),
        root_dir=str(tmp_path),
    )
    session_id = "target-local-evidence"

    hooks.post_tool_call("read_file", {"path": "a.py"}, "print('a')\n", session_id=session_id)
    decision = hooks.pre_tool_call(
        "write_file",
        {"path": "b.py", "content": "print('new b')\n"},
        session_id=session_id,
    )

    assert decision is not None
    assert decision["action"] == "block"
    assert "broad_evidence" in decision["message"]
    state = hooks.explain_state(session_id)
    assert "a.py" in state["evidence_paths"]
    assert "b.py" not in state["evidence_paths"]


def test_default_broad_evidence_records_advisory_without_block(tmp_path: Path) -> None:
    observed = tmp_path / "a.py"
    target = tmp_path / "b.py"
    observed.write_text("print('a')\n")
    target.write_text("print('b')\n")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "default-broad-evidence-advisory"

    hooks.post_tool_call("read_file", {"path": "a.py"}, "print('a')\n", session_id=session_id)
    decision = hooks.pre_tool_call(
        "write_file",
        {"path": "b.py", "content": "print('new b')\n"},
        session_id=session_id,
    )

    assert decision is None
    state = hooks.explain_state(session_id)
    assert state["last_advisory"]["reason"] == "broad_evidence"
    assert "a.py" in state["evidence_paths"]
    assert "b.py" not in state["evidence_paths"]
    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "Proofrail advisory [broad_evidence]" in context


def test_default_low_signal_repeat_records_advisory_without_block(tmp_path: Path) -> None:
    hooks = build_runtime_hooks(
        settings=PluginSettings(low_signal_block_threshold=2),
        root_dir=str(tmp_path),
    )
    session_id = "default-low-signal-advisory"
    args = {"pattern": "needle", "path": "."}

    hooks.post_tool_call("search_files", args, {"matches": []}, session_id=session_id)
    hooks.post_tool_call("search_files", args, {"matches": []}, session_id=session_id)
    decision = hooks.pre_tool_call("search_files", args, session_id=session_id)

    assert decision is None
    state = hooks.explain_state(session_id)
    assert state["consecutive_low_signal"] >= 2
    assert state["last_advisory"]["reason"] == "low_signal_repeat"
    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "Proofrail advisory [low_signal_repeat]" in context


def test_dangerous_warn_allows_with_advisory(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    hooks = build_runtime_hooks(
        settings=PluginSettings(dangerous_command_action="warn", audit_log_path=str(audit_path)),
        root_dir=str(tmp_path),
    )

    decision = hooks.pre_tool_call(
        "terminal",
        {"command": "git push --force"},
        session_id="dangerous-warn-advisory",
    )

    assert decision is None
    state = hooks.explain_state("dangerous-warn-advisory")
    assert any(item["reason"] == "dangerous_command" for item in state["advisories"])
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(entry.get("event") == "dangerous_command" for entry in entries)
    assert any(entry.get("event") == "advisory" and entry.get("reason") == "dangerous_command" for entry in entries)


def test_dangerous_warn_context_survives_followup_workflow_advisory(tmp_path: Path) -> None:
    hooks = build_runtime_hooks(
        settings=PluginSettings(dangerous_command_action="warn"),
        root_dir=str(tmp_path),
    )
    session_id = "dangerous-warn-related-context"

    decision = hooks.pre_tool_call(
        "terminal",
        {"command": "git push --force"},
        session_id=session_id,
    )

    assert decision is None
    state = hooks.explain_state(session_id)
    assert [item["reason"] for item in state["advisories"]][-2:] == ["dangerous_command", "missing_evidence"]
    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "Proofrail advisory [missing_evidence]" in context
    assert "related advisory [dangerous_command]" in context


def test_validation_policy_off_does_not_track_pending_verification(tmp_path: Path) -> None:
    hooks = build_runtime_hooks(
        settings=PluginSettings(validation_policy="off"),
        root_dir=str(tmp_path),
    )
    session_id = "validation-policy-off"

    assert hooks.pre_tool_call("write_file", {"path": "new.py", "content": "print('new')\n"}, session_id=session_id) is None
    hooks.post_tool_call(
        "write_file",
        {"path": "new.py", "content": "print('new')\n"},
        {"success": True},
        session_id=session_id,
    )

    state = hooks.explain_state(session_id)
    assert state["mutation_count"] == 1
    assert state["pending_verification"] is False
    assert state["unverified_mutation_count"] == 0
    assert state["mutation_batch_id"] is None
    assert state["forced_next_mode"] == "none"


def test_dangerous_block_blocks() -> None:
    hooks = build_runtime_hooks(settings=PluginSettings(dangerous_command_action="block"))

    decision = hooks.pre_tool_call(
        "terminal",
        {"command": "git push --force"},
        session_id="dangerous-block",
    )

    assert decision is not None
    assert decision["action"] == "block"
    assert "High-risk command blocked" in decision["message"]


def test_error_preserving_summary_keeps_failed_line() -> None:
    failed_line = "FAILED tests/test_middle.py::test_critical - AssertionError: important failure"
    text = "A" * 5000 + "\n" + failed_line + "\n" + "B" * 5000

    summarized = summarize_large_output(text, threshold=1000)

    assert "chars omitted" in summarized
    assert failed_line in summarized
