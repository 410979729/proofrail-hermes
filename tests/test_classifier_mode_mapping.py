from __future__ import annotations

from pathlib import Path

import pytest

from proofrail import build_runtime_hooks
from proofrail.classifier import GuardrailClassifierDecision
from proofrail.session_state import STATE_STORE


@pytest.fixture(autouse=True)
def _clear_modes() -> None:
    STATE_STORE.clear("classifier-change-strategy")
    STATE_STORE.clear("classifier-user-choice")
    STATE_STORE.clear("classifier-target-state")
    STATE_STORE.clear("classifier-narrow-validation")


def test_classifier_warn_strategy_shift_sets_change_strategy_mode(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("print('old')\n")

    def fake_classifier(**_kwargs):
        return GuardrailClassifierDecision(
            decision="warn",
            reason="The current probe shape is exhausted; switch strategy without broadening scope.",
            evidence_gap="strategy_shift",
            guidance=(
                "switch to a different target-local probe shape",
                "stay on the same target",
            ),
            source="test",
        )

    hooks = build_runtime_hooks(root_dir=str(tmp_path), classifier=fake_classifier)
    session_id = "classifier-change-strategy"
    hooks.post_tool_call("read_file", {"path": "module.py"}, "print('old')\n", session_id=session_id)

    decision = hooks.pre_tool_call(
        "write_file",
        {"path": "module.py", "content": "print('new')\n"},
        session_id=session_id,
    )

    assert decision is None
    state = hooks.explain_state(session_id)
    assert state["forced_next_mode"] == "change_strategy"
    assert state["forced_next_target"] == "module.py"
    assert any("different target-local probe" in item for item in state["allowed_next_actions"])

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "current proofrail mode: change_strategy" in context
    assert "current subgoal: change probe strategy without broadening scope" in context
    assert "switch to a different target-local probe shape" in context


def test_classifier_block_target_state_sets_gather_target_evidence_mode_and_blocks_mutation(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("print('old')\n")

    def fake_classifier(**_kwargs):
        return GuardrailClassifierDecision(
            decision="block",
            reason="Current evidence is still broad. Inspect the target file directly before editing.",
            evidence_gap="target_state",
            guidance=(
                "Inspect module.py directly before editing.",
                "Keep the next change minimal and validate it immediately after the mutation.",
            ),
            source="test",
        )

    hooks = build_runtime_hooks(root_dir=str(tmp_path), classifier=fake_classifier)
    session_id = "classifier-target-state"
    hooks.post_tool_call("read_file", {"path": "module.py"}, "print('old')\n", session_id=session_id)
    hooks.post_tool_call("search_files", {"pattern": "module", "path": str(tmp_path)}, "module.py", session_id=session_id)

    blocked = hooks.pre_tool_call(
        "write_file",
        {"path": "module.py", "content": "print('new')\n"},
        session_id=session_id,
    )

    assert blocked is not None
    assert blocked["action"] == "block"
    assert "Blocked by Proofrail [llm_classifier]" in blocked["message"]
    assert "Proofrail mode switch: gather_target_evidence" in blocked["message"]
    assert "Target: module.py" in blocked["message"]
    assert "Inspect module.py directly before editing." in blocked["message"]

    state = hooks.explain_state(session_id)
    assert state["forced_next_mode"] == "gather_target_evidence"
    assert state["forced_next_target"] == "module.py"

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "current proofrail mode: gather_target_evidence" in context
    assert "current subgoal: inspect the real target before mutating it" in context


def test_classifier_block_narrow_validation_sets_validate_only_mode_and_blocks_mutation(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("print('old')\n")

    def fake_classifier(**_kwargs):
        return GuardrailClassifierDecision(
            decision="block",
            reason="The last change needs a narrower validation before more mutation.",
            evidence_gap="narrow_validation",
            guidance=(
                "Read back module.py directly before any further mutation.",
                "Run one narrow validation tied to module.py.",
            ),
            source="test",
        )

    hooks = build_runtime_hooks(root_dir=str(tmp_path), classifier=fake_classifier)
    session_id = "classifier-narrow-validation"
    hooks.post_tool_call("read_file", {"path": "module.py"}, "print('old')\n", session_id=session_id)

    blocked = hooks.pre_tool_call(
        "write_file",
        {"path": "module.py", "content": "print('new')\n"},
        session_id=session_id,
    )

    assert blocked is not None
    assert blocked["action"] == "block"
    assert "Blocked by Proofrail [llm_classifier]" in blocked["message"]
    assert "Proofrail mode switch: validate_only" in blocked["message"]
    assert "Target: module.py" in blocked["message"]
    assert "Read back module.py directly before any further mutation." in blocked["message"]

    state = hooks.explain_state(session_id)
    assert state["forced_next_mode"] == "validate_only"
    assert state["forced_next_target"] == "module.py"

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "current proofrail mode: validate_only" in context
    assert "current subgoal: verify the last change on module.py" in context


def test_classifier_ask_user_sets_user_choice_mode_and_blocks_mutation(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"mode": "old"}\n')
    observed = 'mode=old\n'

    def fake_classifier(**_kwargs):
        return GuardrailClassifierDecision(
            decision="ask_user",
            reason="Two valid mutation paths exist and the choice changes user-visible behavior.",
            evidence_gap="user_choice",
            guidance=(
                "ask the user which behavior they want",
                "do not mutate until the choice is explicit",
            ),
            source="test",
        )

    hooks = build_runtime_hooks(root_dir=str(tmp_path), classifier=fake_classifier)
    session_id = "classifier-user-choice"
    hooks.post_tool_call("read_file", {"path": "config.json"}, observed, session_id=session_id)
    hooks.post_tool_call("read_file", {"path": "config.json"}, observed, session_id=session_id)

    blocked = hooks.pre_tool_call(
        "write_file",
        {"path": "config.json", "content": '{"mode": "new"}\n'},
        session_id=session_id,
    )

    assert blocked is not None
    assert blocked["action"] == "block"
    assert "Blocked by Proofrail [llm_classifier]" in blocked["message"]
    assert "Target: config.json" in blocked["message"]
    assert "Smallest next action:" in blocked["message"]
    assert "ask the user which behavior they want" in blocked["message"]

    state = hooks.explain_state(session_id)
    assert state["forced_next_mode"] == "user_choice"
    assert state["forced_next_target"] == "config.json"

    context = hooks.pre_llm_call(session_id=session_id)["context"]
    assert "current proofrail mode: user_choice" in context
    assert "current subgoal: wait for an explicit user decision before continuing" in context
    assert "ask the user which behavior they want" in context
