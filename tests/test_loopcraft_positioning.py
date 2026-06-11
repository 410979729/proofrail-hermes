from __future__ import annotations

from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.models import PluginSettings
from proofrail.session_state import STATE_STORE
from proofrail.task_ledger import loopcraft_methodology_step, task_snapshot


@pytest.fixture(autouse=True)
def _isolate_default_audit_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    STATE_STORE.clear("loopcraft-full-context")
    STATE_STORE.clear("loopcraft-ledger")


def test_full_context_positions_runtime_as_loopcraft_loop_engineering(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    hooks = build_runtime_hooks(
        settings=PluginSettings(advisory_injection="full"),
        root_dir=str(tmp_path),
    )

    assert hooks.pre_tool_call(
        "write_file",
        {"path": "app.py", "content": "print('new')\n"},
        session_id="loopcraft-full-context",
    ) is None

    context = hooks.pre_llm_call(session_id="loopcraft-full-context")["context"]
    assert "LoopCraft" in context
    assert "loop engineering" in context
    assert "skill/methodology check" in context
    assert "observe → plan → act → verify → closeout" in context


def test_task_snapshot_exposes_loopcraft_methodology_step(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "loopcraft-ledger"

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    state = STATE_STORE.get(session_id)
    snapshot = task_snapshot(state)

    assert snapshot["loopcraft_step"] == "plan_smallest_change"
    assert snapshot["loopcraft_next_action"] == "make the smallest explainable change, then validate it immediately"
    assert loopcraft_methodology_step(state)[0] == "plan_smallest_change"


def test_task_snapshot_includes_advisory_only_task_understanding(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    hooks = build_runtime_hooks(root_dir=str(tmp_path))
    session_id = "loopcraft-understanding-snapshot"

    hooks.post_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, "ok", session_id=session_id)
    snapshot = task_snapshot(STATE_STORE.get(session_id))

    assert snapshot["task_understanding"]["action_stage"] == "verify"
    assert snapshot["task_understanding"]["evidence_state"] == "needs_validation"
    assert snapshot["task_understanding"]["advisory_only"] is True
    assert snapshot["task_understanding"]["control_effects"] == []


def test_pre_llm_context_includes_agent_self_routing_checkpoint(tmp_path: Path) -> None:
    hooks = build_runtime_hooks(settings=PluginSettings(advisory_injection="full"), root_dir=str(tmp_path))
    session_id = "loopcraft-understanding-context"

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    context = hooks.pre_llm_call(session_id=session_id)["context"]

    assert "LOOPCRAFT AGENT SELF-ROUTING CHECKPOINT" in context
    assert "advisory only" in context.lower()
    assert "not a permission decision" in context.lower()
    assert "The agent decides" in context
    assert "intent / domain / risk / stage / next" in context


def test_pre_llm_context_uses_user_text_to_prompt_agent_self_routing(tmp_path: Path) -> None:
    hooks = build_runtime_hooks(root_dir=str(tmp_path))

    context = hooks.pre_llm_call(
        session_id="loopcraft-user-text-context",
        user_message="看下这个 PR 有没有问题，不要改",
    )["context"]

    assert "LOOPCRAFT AGENT SELF-ROUTING CHECKPOINT" in context
    assert "The agent decides" in context
    assert "User intent: `review_only`" not in context
    assert "Side-effect risk: `read_only`" not in context
    assert "Control effects: none" in context
