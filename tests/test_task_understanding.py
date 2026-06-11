from __future__ import annotations

from proofrail.models import SessionRuntimeState
from proofrail.task_understanding import analyze_task, render_task_understanding_context


def test_review_only_user_intent_overrides_mutation_guess() -> None:
    understanding = analyze_task("看下这个 PR 有没有问题，不要改")

    assert understanding.user_intent == "review_only"
    assert "github_pr" in understanding.domains
    assert understanding.side_effect_risk == "read_only"
    assert understanding.action_stage == "understand"
    assert understanding.advisory_only is True
    assert understanding.control_effects == ()
    assert understanding.confidence >= 0.8
    assert "do not mutate" in understanding.recommended_next_action.lower()


def test_mixed_pr_ci_task_keeps_diagnosis_before_possible_code_change() -> None:
    understanding = analyze_task("处理仓库里的pr2,然后复查失败的ci run")

    assert understanding.user_intent == "execute_change"
    assert {"github_pr", "ci", "code"}.issubset(set(understanding.domains))
    assert understanding.side_effect_risk == "local_write_possible"
    assert understanding.action_stage == "understand"
    assert understanding.advisory_only is True
    assert understanding.control_effects == ()
    assert "ci" in understanding.recommended_next_action.lower()
    assert "before editing" in understanding.recommended_next_action.lower()


def test_ops_repair_distinguishes_service_check_from_immediate_restart() -> None:
    understanding = analyze_task("天枢服务又不回消息了，检查一下")

    assert understanding.user_intent == "ops_repair"
    assert {"service", "messaging_platform"}.issubset(set(understanding.domains))
    assert understanding.side_effect_risk == "service_affecting_possible"
    assert understanding.action_stage == "understand"
    assert "status" in understanding.recommended_next_action.lower()
    assert "restart" not in understanding.recommended_next_action.lower().split("before", 1)[0]


def test_ambiguous_task_falls_back_to_uncertain_read_only_orientation() -> None:
    understanding = analyze_task("看看这个仓库")

    assert understanding.user_intent == "uncertain"
    assert understanding.confidence <= 0.5
    assert understanding.side_effect_risk == "read_only"
    assert understanding.action_stage == "understand"
    assert understanding.uncertainty_reasons
    assert understanding.advisory_only is True
    assert understanding.control_effects == ()


def test_runtime_pending_verification_sets_verify_stage_without_control_effects() -> None:
    state = SessionRuntimeState(
        pending_verification=True,
        mutation_count=1,
        last_mutation_label="write: proofrail/task_understanding.py",
        touched_files=("proofrail/task_understanding.py",),
    )

    understanding = analyze_task("继续", state=state)

    assert understanding.action_stage == "verify"
    assert understanding.evidence_state == "needs_validation"
    assert understanding.user_intent == "continue_existing"
    assert understanding.advisory_only is True
    assert understanding.control_effects == ()
    assert "validate" in understanding.recommended_next_action.lower()


def test_rendered_task_understanding_context_is_agent_self_routing_checkpoint() -> None:
    context = render_task_understanding_context(analyze_task("调研一下同类插件有哪些功能"))

    assert "LOOPCRAFT AGENT SELF-ROUTING CHECKPOINT" in context
    assert "advisory only" in context.lower()
    assert "not a permission decision" in context.lower()
    assert "The agent decides" in context
    assert "intent / domain / risk / stage / next" in context
    assert "Control effects: none" in context
    assert "User intent: `research`" not in context
    assert "Confidence:" not in context
