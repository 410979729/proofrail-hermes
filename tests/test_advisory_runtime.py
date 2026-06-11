from __future__ import annotations

import json
from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.models import PluginSettings
from proofrail.session_state import STATE_STORE, set_forced_next_mode
from proofrail.settings import settings_from_mapping
from proofrail.summarize import summarize_large_output
from proofrail.tooling import get_tool_category


@pytest.fixture(autouse=True)
def _isolate_default_audit_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def _audit_entries(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_advisory_first_defaults_are_snake_case() -> None:
    settings = settings_from_mapping({})

    assert settings.enforcement_mode == "advisory"
    assert settings.advisory_injection == "compact"
    assert settings.validation_policy == "batch"
    assert settings.mutation_batch_max == 5
    assert settings.dangerous_command_action == "warn"


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
    entries = _audit_entries(audit_path)
    assert any(entry.get("event") == "advisory" and entry.get("reason") == "missing_evidence" for entry in entries)


def test_full_advisory_injection_renders_task_panel(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(advisory_injection="full"),
        root_dir=str(tmp_path),
    )
    session_id = "full-advisory-injection"

    assert hooks.pre_tool_call(
        "write_file",
        {"path": "app.py", "content": "print('new')\n"},
        session_id=session_id,
    ) is None

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "## [LOOPCRAFT TASK PANEL — not user input]" in context
    assert "LoopCraft" in context
    assert "loop engineering" in context
    assert "## [SYSTEM STATUS — not user input]" in context
    assert "## [PROOFRAIL ADVISORY — not user input]" in context
    assert "Proofrail advisory [missing_evidence]" in context
    assert "fastest next action: inspect app.py directly" in context


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


def test_forced_validate_only_is_context_hint_in_advisory_mode(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    session_id = "forced-validate-only-advisory"
    STATE_STORE.clear(session_id)
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="advisory"),
        root_dir=str(tmp_path),
    )
    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    set_forced_next_mode(
        session_id,
        mode="validate_only",
        target="app.py",
        why="Previous state requested validation.",
        exit_condition="Validate app.py.",
    )

    decision = hooks.pre_tool_call(
        "write_file",
        {"path": "app.py", "content": "print('new')\n"},
        session_id=session_id,
    )

    assert decision is None
    state = hooks.explain_state(session_id)
    assert state["forced_next_mode"] == "validate_only"
    assert state["last_advisory"]["reason"] == "pending_verification"
    assert state["last_advisory"]["mode"] == "validate_only"


def test_default_tool_aliases_cover_common_host_spellings() -> None:
    write_aliases = [
        "apply_patch",
        "edit_file",
        "file_edit",
        "file_write",
        "create_file",
        "update_file",
        "str_replace_editor",
    ]
    exec_aliases = ["bash", "shell", "run_command", "exec"]
    search_aliases = ["grep", "grep_search", "glob", "rg", "search_files"]
    network_aliases = ["web_fetch", "fetch_url", "browser_fetch"]

    for alias in write_aliases:
        assert get_tool_category(alias) == "write"
    for alias in exec_aliases:
        assert get_tool_category(alias) == "exec"
    for alias in search_aliases:
        assert get_tool_category(alias) == "search"
    for alias in network_aliases:
        assert get_tool_category(alias) == "network"


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
    entries = _audit_entries(audit_path)
    assert any(entry.get("event") == "dangerous_command" for entry in entries)
    assert any(entry.get("event") == "advisory" and entry.get("reason") == "dangerous_command" for entry in entries)


def test_guarded_warn_blocks_critical_dangerous_command() -> None:
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="guarded", dangerous_command_action="warn"),
    )

    decision = hooks.pre_tool_call(
        "terminal",
        {"command": "git reset --hard"},
        session_id="guarded-critical-dangerous",
    )

    assert decision is not None
    assert decision["action"] == "block"
    assert "guarded" in decision["message"].lower()
    assert "git reset --hard" in decision["message"]


def test_guarded_allow_allows_critical_dangerous_command() -> None:
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="guarded", dangerous_command_action="allow"),
    )

    decision = hooks.pre_tool_call(
        "terminal",
        {"command": "git reset --hard"},
        session_id="guarded-critical-allow",
    )

    assert decision is None


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


def test_missing_evidence_advisory_ignored_is_audited_after_blind_mutation(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('old')\n")
    audit_path = tmp_path / "missing-ignored.jsonl"
    session_id = "missing-evidence-ignored"
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="advisory", audit_log_path=str(audit_path)),
        root_dir=str(tmp_path),
    )

    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, session_id=session_id) is None
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, {"success": True}, session_id=session_id)

    state = hooks.explain_state(session_id)
    assert state["ignored_advisory_count"] == 1
    assert state["last_advisory"]["ignored"] is True
    entries = _audit_entries(audit_path)
    assert any(entry.get("event") == "advisory_ignored" and entry.get("reason") == "missing_evidence" for entry in entries)


def test_pending_verification_advisory_ignored_is_audited_after_more_mutation(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('old')\n")
    audit_path = tmp_path / "pending-ignored.jsonl"
    session_id = "pending-verification-ignored"
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="advisory", audit_log_path=str(audit_path)),
        root_dir=str(tmp_path),
    )

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, session_id=session_id) is None
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, {"success": True}, session_id=session_id)
    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "print('newer')\n"}, session_id=session_id) is None
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "print('newer')\n"}, {"success": True}, session_id=session_id)

    state = hooks.explain_state(session_id)
    assert state["ignored_advisory_count"] == 1
    assert state["last_advisory"]["ignored"] is True
    entries = _audit_entries(audit_path)
    assert any(entry.get("event") == "advisory_ignored" and entry.get("reason") == "pending_verification" for entry in entries)


def test_low_signal_repeat_advisory_ignored_is_audited_after_same_probe(tmp_path: Path) -> None:
    audit_path = tmp_path / "low-signal-ignored.jsonl"
    session_id = "low-signal-repeat-ignored"
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="advisory", low_signal_block_threshold=2, audit_log_path=str(audit_path)),
        root_dir=str(tmp_path),
    )
    args = {"pattern": "definitely-not-here", "path": str(tmp_path)}

    hooks.post_tool_call("search_files", args, "0 matches", session_id=session_id)
    hooks.post_tool_call("search_files", args, "0 matches", session_id=session_id)
    assert hooks.pre_tool_call("search_files", args, session_id=session_id) is None
    state = hooks.explain_state(session_id)
    assert state["last_advisory"]["reason"] == "low_signal_repeat"

    hooks.post_tool_call("search_files", args, "0 matches", session_id=session_id)

    state = hooks.explain_state(session_id)
    assert state["ignored_advisory_count"] == 1
    assert state["last_advisory"]["ignored"] is True
    entries = _audit_entries(audit_path)
    assert any(entry.get("event") == "advisory_ignored" and entry.get("reason") == "low_signal_repeat" for entry in entries)


def test_validation_policy_batch_allows_until_batch_limit_in_strict(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('old')\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="strict", validation_policy="batch", mutation_batch_max=2),
        root_dir=str(tmp_path),
    )
    session_id = "strict-batch-validation-policy"

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "one\n"}, session_id=session_id) is None
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "one\n"}, {"success": True}, session_id=session_id)

    second = hooks.pre_tool_call("write_file", {"path": "app.py", "content": "two\n"}, session_id=session_id)
    assert second is None
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "two\n"}, {"success": True}, session_id=session_id)

    third = hooks.pre_tool_call("write_file", {"path": "app.py", "content": "three\n"}, session_id=session_id)
    assert third is not None
    assert third["action"] == "block"
    assert "pending_verification" in third["message"]


def test_validation_policy_after_each_mutation_blocks_next_mutation_in_strict(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('old')\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="strict", validation_policy="after_each_mutation"),
        root_dir=str(tmp_path),
    )
    session_id = "strict-after-each-validation-policy"

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "one\n"}, session_id=session_id) is None
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "one\n"}, {"success": True}, session_id=session_id)

    second = hooks.pre_tool_call("write_file", {"path": "app.py", "content": "two\n"}, session_id=session_id)
    assert second is not None
    assert second["action"] == "block"


def test_validation_policy_after_each_mutation_escalates_advisory_severity(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('old')\n")
    session_id = "advisory-after-each-validation-policy"
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="advisory", validation_policy="after_each_mutation", mutation_batch_max=5),
        root_dir=str(tmp_path),
    )

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "one\n"}, session_id=session_id) is None
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "one\n"}, {"success": True}, session_id=session_id)

    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "two\n"}, session_id=session_id) is None
    state = hooks.explain_state(session_id)
    assert state["last_advisory"]["reason"] == "pending_verification"
    assert state["last_advisory"]["severity"] == "risk"


def test_unknown_target_mutating_exec_records_advisory(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('old')\n")
    audit_path = tmp_path / "unknown-target.jsonl"
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="advisory", audit_log_path=str(audit_path)),
        root_dir=str(tmp_path),
    )
    session_id = "unknown-target-mutating-exec"

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    decision = hooks.pre_tool_call(
        "terminal",
        {"command": "npm install left-pad"},
        session_id=session_id,
    )

    assert decision is None
    state = hooks.explain_state(session_id)
    assert state["last_advisory"]["reason"] == "unknown_target_mutation"
    entries = _audit_entries(audit_path)
    assert any(entry.get("event") == "advisory" and entry.get("reason") == "unknown_target_mutation" for entry in entries)


def test_advisory_injection_off_keeps_compact_context_without_advisory_card(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('old')\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(enforcement_mode="advisory", advisory_injection="off"),
        root_dir=str(tmp_path),
    )
    session_id = "advisory-injection-off"

    assert hooks.pre_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, session_id=session_id) is None
    context = hooks.pre_llm_call(session_id=session_id)["context"]

    assert "PROOFRAIL ADVISORY" not in context
    assert "SYSTEM STATUS" in context


def test_error_preserving_summary_keeps_failed_line() -> None:
    failed_line = "FAILED tests/test_middle.py::test_critical - AssertionError: important failure"
    text = "A" * 5000 + "\n" + failed_line + "\n" + "B" * 5000

    summarized = summarize_large_output(text, threshold=1000)

    assert "chars omitted" in summarized
    assert failed_line in summarized
