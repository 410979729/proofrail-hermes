from __future__ import annotations

import json
from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.classifier import GuardrailClassifierDecision
from proofrail.models import PluginSettings
from proofrail.session_state import STATE_STORE


@pytest.fixture(autouse=True)
def _isolate_default_audit_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    STATE_STORE.clear("phase4.change-strategy")
    STATE_STORE.clear("phase4.user-choice")


def test_change_strategy_panel_uses_progress_framing(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("print('old')\n")

    def fake_classifier(**_kwargs):
        return GuardrailClassifierDecision(
            decision="warn",
            reason="The current probe shape is exhausted; switch strategy without broadening scope.",
            evidence_gap="strategy_shift",
            guidance=("switch to a different target-local probe shape",),
            source="test",
        )

    hooks = build_runtime_hooks(root_dir=str(tmp_path), classifier=fake_classifier)
    session_id = "phase4.change-strategy"
    hooks.post_tool_call("read_file", {"path": "module.py"}, "print('old')\n", session_id=session_id)
    assert hooks.pre_tool_call("write_file", {"path": "module.py", "content": "print('new')\n"}, session_id=session_id) is None

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "Complete this subtask to reopen forward progress." in context
    assert "This mode is a collaboration handoff, not a failure state." in context
    assert "Choose the first smallest next action if the next move is unclear." in context


def test_forced_mode_transition_is_audited(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    target = tmp_path / "config.json"
    target.write_text('{"mode": "old"}\n')

    def fake_classifier(**_kwargs):
        return GuardrailClassifierDecision(
            decision="ask_user",
            reason="Two valid mutation paths exist and the choice changes user-visible behavior.",
            evidence_gap="user_choice",
            guidance=("ask the user which behavior they want",),
            source="test",
        )

    hooks = build_runtime_hooks(
        settings=PluginSettings(audit_log_path=str(audit_path)),
        root_dir=str(tmp_path),
        classifier=fake_classifier,
    )
    session_id = "phase4.user-choice"
    hooks.post_tool_call("read_file", {"path": "config.json"}, "mode=old\n", session_id=session_id)
    hooks.post_tool_call("read_file", {"path": "config.json"}, "mode=old\n", session_id=session_id)

    blocked = hooks.pre_tool_call(
        "write_file",
        {"path": "config.json", "content": '{"mode": "new"}\n'},
        session_id=session_id,
    )
    assert blocked is not None

    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    forced_entries = [entry for entry in entries if entry.get("event") == "forced_mode_transition"]
    assert forced_entries
    latest = forced_entries[-1]
    assert latest["mode"] == "user_choice"
    assert latest["target"] == "config.json"
    assert latest["reason"] == "Two valid mutation paths exist and the choice changes user-visible behavior."
