from __future__ import annotations

from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.session_state import STATE_STORE


@pytest.fixture(autouse=True)
def _isolate_default_audit_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default audit logs out of the repository during tests."""
    monkeypatch.chdir(tmp_path)


def _record_write_with_pending_verification(hooks, session_id: str, path: str) -> None:
    hooks.post_tool_call("read_file", {"path": path}, "old", session_id=session_id)
    hooks.post_tool_call(
        "write_file",
        {"path": path, "content": "new"},
        {"success": True},
        session_id=session_id,
    )
    assert hooks.explain_state(session_id)["pending_verification"] is True


def test_read_file_on_touched_path_clears_pending_verification(tmp_path: Path) -> None:
    STATE_STORE.clear("readback-read-file-clears")
    target = tmp_path / "module.py"
    target.write_text("old")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "readback-read-file-clears"
    _record_write_with_pending_verification(hooks, session_id, "module.py")

    hooks.post_tool_call("read_file", {"path": "module.py"}, "new", session_id=session_id)

    state = hooks.explain_state(session_id)
    assert state["pending_verification"] is False
    assert state["validation_count"] == 1


def test_terminal_cat_on_touched_path_clears_pending_verification(tmp_path: Path) -> None:
    STATE_STORE.clear("readback-terminal-cat-clears")
    target = tmp_path / "module.py"
    target.write_text("old")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "readback-terminal-cat-clears"
    _record_write_with_pending_verification(hooks, session_id, "module.py")

    hooks.post_tool_call(
        "terminal",
        {"command": "cat module.py"},
        {"exit_code": 0, "stdout": "new"},
        session_id=session_id,
    )

    state = hooks.explain_state(session_id)
    assert state["pending_verification"] is False
    assert state["validation_count"] == 1


def test_read_file_on_unrelated_path_does_not_clear_pending_verification(tmp_path: Path) -> None:
    STATE_STORE.clear("readback-unrelated-keeps-pending")
    target = tmp_path / "module.py"
    unrelated = tmp_path / "README.md"
    target.write_text("old")
    unrelated.write_text("docs")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "readback-unrelated-keeps-pending"
    _record_write_with_pending_verification(hooks, session_id, "module.py")

    hooks.post_tool_call("read_file", {"path": "README.md"}, "docs", session_id=session_id)

    state = hooks.explain_state(session_id)
    assert state["pending_verification"] is True
    assert state["validation_count"] == 0


def test_failed_mutating_exec_after_validation_does_not_reenter_pending_verification(tmp_path: Path) -> None:
    STATE_STORE.clear("failed-mutating-exec-stays-clear")
    target = tmp_path / "docs" / "architecture.md"
    target.parent.mkdir()
    target.write_text("old")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "failed-mutating-exec-stays-clear"

    _record_write_with_pending_verification(hooks, session_id, "docs/architecture.md")
    hooks.post_tool_call("read_file", {"path": "docs/architecture.md"}, "new", session_id=session_id)
    assert hooks.explain_state(session_id)["pending_verification"] is False

    hooks.post_tool_call(
        "terminal",
        {"command": "python3 -m pip install --user build"},
        {"exit_code": 1, "stdout": "error: externally-managed-environment"},
        session_id=session_id,
    )

    state = hooks.explain_state(session_id)
    assert state["pending_verification"] is False
    assert state["forced_next_mode"] == "none"
    assert state["last_mutation_label"] is None
    assert hooks.pre_tool_call("terminal", {"command": "python3 -m venv .venv.release"}, session_id=session_id) is None
