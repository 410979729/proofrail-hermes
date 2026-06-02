from __future__ import annotations

from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.session_state import STATE_STORE, set_forced_next_mode
from proofrail.validation import changed_path_hints


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


def test_command_path_hints_ignore_shell_assignment_and_dev_null_redirection() -> None:
    hints = changed_path_hints(
        "terminal",
        {},
        "PLUGIN=/home/a/project/plugin npm install --no-save pkg 2>/dev/null",
    )

    assert "PLUGIN=/home/a/project/plugin" not in hints
    assert "/home/a/project/plugin" not in hints
    assert "2>/dev/null" not in hints
    assert "/dev/null" not in hints


def test_command_path_hints_keep_real_stdout_redirection_target() -> None:
    assert changed_path_hints("terminal", {}, "printf ok >/tmp/proofrail-out.txt") == ["/tmp/proofrail-out.txt"]


def test_directory_target_can_be_cleared_by_child_readback(tmp_path: Path) -> None:
    STATE_STORE.clear("directory-target-child-readback")
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    child = target_dir / "auth.json"
    child.write_text("{}")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "directory-target-child-readback"

    hooks.post_tool_call("read_file", {"path": "project/auth.json"}, "{}", session_id=session_id)
    hooks.post_tool_call(
        "execute_code",
        {"code": f"from pathlib import Path\nPath({str(target_dir)!r}).mkdir(exist_ok=True)\n"},
        {"status": "success", "output": "ok"},
        session_id=session_id,
    )
    # Force the historical bad state: a coarse directory target in validate_only.
    set_forced_next_mode(
        session_id,
        mode="validate_only",
        target=str(target_dir),
        why="regression fixture",
        exit_condition="validation complete",
    )

    def poison_with_directory_target(s) -> None:
        s.pending_verification = True
        s.touched_files = (str(target_dir),)

    state = STATE_STORE.update(session_id, poison_with_directory_target)
    assert state.pending_verification is True

    hooks.post_tool_call("read_file", {"path": "project/auth.json"}, "{}", session_id=session_id)

    state_after = hooks.explain_state(session_id)
    assert state_after["pending_verification"] is False
    assert state_after["forced_next_mode"] == "none"
