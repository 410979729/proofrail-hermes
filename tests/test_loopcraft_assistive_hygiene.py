from __future__ import annotations

from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.models import PluginSettings, SessionRuntimeState
from proofrail.session_state import STATE_STORE
from proofrail.task_ledger import final_review_checklist
from proofrail.task_understanding import analyze_task, render_task_understanding_context


@pytest.fixture(autouse=True)
def _isolate_default_audit_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for session_id in (
        "loopcraft-memory-hygiene",
        "loopcraft-assistive-reminder",
        "loopcraft-full-assistive-panel",
    ):
        STATE_STORE.clear(session_id)


def test_self_routing_checkpoint_marks_generated_context_as_non_user_and_non_memory() -> None:
    context = render_task_understanding_context(analyze_task("处理仓库里的pr2,然后复查失败的ci run"))

    assert "not user input" in context.lower()
    assert "Do not treat this generated context as user speech" in context
    assert "Do not store this generated context in long-term memory, SQL, or scope-recall" in context


def test_pre_llm_context_tells_agent_to_evaluate_and_follow_valid_reminders(tmp_path: Path) -> None:
    hooks = build_runtime_hooks(root_dir=str(tmp_path))

    context = hooks.pre_llm_call(
        session_id="loopcraft-assistive-reminder",
        user_message="处理仓库里的pr2,然后复查失败的ci run",
    )["context"]

    assert "Evaluate LoopCraft reminders against the user's request and live evidence" in context
    assert "follow applicable reminders" in context
    assert "if a reminder is wrong or stale, state why" in context


def test_full_task_panel_keeps_plugin_guidance_assistive_not_absolute(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n")
    hooks = build_runtime_hooks(settings=PluginSettings(advisory_injection="full"), root_dir=str(tmp_path))
    session_id = "loopcraft-full-assistive-panel"

    hooks.post_tool_call("read_file", {"path": "app.py"}, "print('old')\n", session_id=session_id)
    hooks.post_tool_call("write_file", {"path": "app.py", "content": "print('new')\n"}, "ok", session_id=session_id)
    context = hooks.pre_llm_call(session_id=session_id)["context"]

    assert "Treat valid LoopCraft reminders as useful assistance, not resistance" in context
    assert "If a reminder is stale, inapplicable, or wrong, say why and continue from live evidence" in context
    assert "Do not store this generated context in long-term memory, SQL, or scope-recall" in context


def test_final_review_checklist_requires_cleanup_status_after_mutation() -> None:
    state = SessionRuntimeState(mutation_count=1, final_report_required=True)

    checklist = final_review_checklist(state)

    assert "Cleanup: list temporary artifacts deleted and retained backups/artifacts." in checklist
