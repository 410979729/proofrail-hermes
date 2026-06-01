from __future__ import annotations

from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.session_state import STATE_STORE


@pytest.fixture(autouse=True)
def _isolate_default_audit_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def _record_write_with_pending_verification(hooks, session_id: str, path: str) -> None:
    hooks.post_tool_call("read_file", {"path": path}, "old", session_id=session_id)
    hooks.post_tool_call(
        "write_file",
        {"path": path, "content": "new"},
        {"success": True},
        session_id=session_id,
    )


def test_pending_verification_sets_validate_only_mode_and_action_menu(tmp_path: Path) -> None:
    STATE_STORE.clear("cooperative-validate-mode")
    target = tmp_path / "module.py"
    target.write_text("old")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "cooperative-validate-mode"

    _record_write_with_pending_verification(hooks, session_id, "module.py")

    state = hooks.explain_state(session_id)
    assert state["pending_verification"] is True
    assert state["forced_next_mode"] == "validate_only"
    assert state["forced_next_target"] == "module.py"
    assert "read back module.py directly" in state["allowed_next_actions"]
    assert any("alternate tools" in item for item in state["forbidden_next_actions"])


def test_pending_verification_blocks_unrelated_search_with_validate_only_guidance(tmp_path: Path) -> None:
    STATE_STORE.clear("cooperative-validate-block")
    target = tmp_path / "module.py"
    target.write_text("old")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "cooperative-validate-block"

    _record_write_with_pending_verification(hooks, session_id, "module.py")
    blocked = hooks.pre_tool_call("search_files", {"pattern": "proofrail", "path": str(tmp_path)}, session_id=session_id)

    assert blocked is not None
    assert blocked["action"] == "block"
    assert "Proofrail mode switch: validate_only" in blocked["message"]
    assert "Why blocked now: the last change on module.py is not yet verified" in blocked["message"]
    assert "Current subgoal: verify the last change on module.py" in blocked["message"]
    assert "Smallest next action:" in blocked["message"]
    assert "Done when:" in blocked["message"]
    assert "Avoid right now:" in blocked["message"]
    assert "If unsure:" in blocked["message"]


def test_missing_evidence_sets_target_evidence_mode_and_context_panel(tmp_path: Path) -> None:
    STATE_STORE.clear("cooperative-evidence-mode")
    target = tmp_path / "existing.txt"
    target.write_text("old")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "cooperative-evidence-mode"

    blocked = hooks.pre_tool_call("write_file", {"path": "existing.txt", "content": "new"}, session_id=session_id)

    assert blocked is not None
    state = hooks.explain_state(session_id)
    assert state["forced_next_mode"] == "gather_target_evidence"
    assert state["forced_next_target"] == "existing.txt"

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "current proofrail mode: gather_target_evidence" in context
    assert "current target: existing.txt" in context
    assert "current subgoal: inspect the real target before mutating it" in context
    assert "smallest next action:" in context
    assert "avoid right now:" in context
    assert "This handoff is part of the task, not a refusal." in context
    assert "Choose the first smallest next action if the next move is unclear." in context


def test_readback_clears_forced_mode_after_validation(tmp_path: Path) -> None:
    STATE_STORE.clear("cooperative-clear-mode")
    target = tmp_path / "module.py"
    target.write_text("old")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "cooperative-clear-mode"

    _record_write_with_pending_verification(hooks, session_id, "module.py")
    hooks.post_tool_call("read_file", {"path": "module.py"}, "new", session_id=session_id)

    state = hooks.explain_state(session_id)
    assert state["pending_verification"] is False
    assert state["forced_next_mode"] == "none"
    assert state["forced_next_target"] is None

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "validation complete" in context
    assert "forward progress reopened" in context
