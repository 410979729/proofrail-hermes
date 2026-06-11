"""Task-ledger rendering and summaries for autonomous coding sessions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import SessionRuntimeState
from .task_understanding import analyze_task, render_task_understanding_context


def task_status(state: SessionRuntimeState) -> str:
    """Return a compact status for the autonomous coding task."""
    if state.pending_verification:
        return "needs_validation"
    if state.mutation_count and state.validation_count:
        return "validated"
    if state.mutation_count:
        return "changed_without_validation"
    if state.evidence_count:
        return "ready_to_execute"
    return "needs_evidence"


def loopcraft_methodology_step(state: SessionRuntimeState) -> tuple[str, str]:
    """Return the LoopCraft loop-engineering step and next action.

    This compresses Superpowers-style workflow discipline into runtime state:
    choose the relevant methodology, observe the control path, plan the smallest
    change, act narrowly, verify, and close out with evidence.
    """
    if state.pending_verification:
        return (
            "verify_last_change",
            "validate the last mutation before making more changes",
        )
    if state.mutation_count and state.validation_count:
        return (
            "closeout_or_continue",
            "report evidence or continue with the same observe → plan → act → verify loop",
        )
    if state.mutation_count:
        return (
            "verify_last_change",
            "validate the last mutation before making more changes",
        )
    if state.evidence_count:
        return (
            "plan_smallest_change",
            "make the smallest explainable change, then validate it immediately",
        )
    return (
        "observe_control_path",
        "inspect the closest code, config, log, or test on the control path",
    )


def task_snapshot(state: SessionRuntimeState) -> dict[str, Any]:
    """Build a JSON-safe task ledger snapshot from the runtime state."""
    loopcraft_step, loopcraft_next_action = loopcraft_methodology_step(state)
    task_understanding = analyze_task(state=state)
    task_understanding_snapshot = asdict(task_understanding)
    for key in ("domains", "uncertainty_reasons", "control_effects"):
        task_understanding_snapshot[key] = list(task_understanding_snapshot.get(key, ()))
    return {
        "status": task_status(state),
        "phase": state.phase,
        "loopcraft_step": loopcraft_step,
        "loopcraft_next_action": loopcraft_next_action,
        "task_understanding": task_understanding_snapshot,
        "evidence_count": state.evidence_count,
        "mutation_count": state.mutation_count,
        "validation_count": state.validation_count,
        "dangerous_count": state.dangerous_count,
        "pending_verification": state.pending_verification,
        "final_report_required": state.final_report_required,
        "last_evidence_label": state.last_evidence_label,
        "last_mutation_label": state.last_mutation_label,
        "last_validation_label": state.last_validation_label,
        "last_dangerous_label": state.last_dangerous_label,
        "evidence_labels": list(state.evidence_labels),
        "mutation_labels": list(state.mutation_labels),
        "validation_labels": list(state.validation_labels),
        "dangerous_labels": list(state.dangerous_labels),
        "touched_files": list(state.touched_files),
        "validation_suggestions": list(state.validation_suggestions),
    }


def render_loopcraft_context(state: SessionRuntimeState, task_text: str = "") -> list[str]:
    """Render the compact LoopCraft methodology panel.

    The wording intentionally mirrors the useful parts of Superpowers — skill
    routing, small plans, TDD/verification discipline, review/closeout — but is
    adapted for Proofrail's runtime-hook loop instead of Claude-only skill files.
    """
    step, next_action = loopcraft_methodology_step(state)
    return [
        "## [LOOPCRAFT LOOP — not user input]",
        "- Product: LoopCraft — a Hermes-native loop engineering runtime.",
        "- Methodology source: Superpowers-style composable skills, adapted into runtime feedback.",
        "- Cycle: skill/methodology check → observe → plan → act → verify → closeout.",
        "- Short form: observe → plan → act → verify → closeout.",
        f"- Current loop step: {step}",
        f"- Next loop action: {next_action}.",
        "",
        *render_task_understanding_context(analyze_task(task_text, state=state)).splitlines(),
    ]


def render_task_context(state: SessionRuntimeState) -> str:
    """Render task-ledger context for pre_llm_call injection."""
    lines = [
        *render_loopcraft_context(state),
        "",
        "## [SYSTEM STATUS — task]",
        f"- Status: {task_status(state)}",
    ]
    if state.evidence_labels:
        lines.append("- Recent evidence:")
        lines.extend(f"  - {item}" for item in state.evidence_labels[-5:])
    if state.mutation_labels:
        lines.append("- Recent mutations:")
        lines.extend(f"  - {item}" for item in state.mutation_labels[-5:])
    if state.validation_labels:
        lines.append("- Recent validations:")
        lines.extend(f"  - {item}" for item in state.validation_labels[-5:])
    if state.pending_verification:
        lines.append("- Next step: validate the last mutation before making more changes.")
    elif state.mutation_count and state.validation_count:
        lines.append("- Next step: continue only if each new mutation is followed by immediate validation.")
    elif state.evidence_count:
        lines.append("- Next step: make the smallest explainable change, then validate it immediately.")
    else:
        lines.append("- Next step: inspect the closest code, config, log, or test on the control path.")
    return "\n".join(lines)


def final_review_checklist(state: SessionRuntimeState) -> list[str]:
    """Return the final response checklist for sessions with mutations."""
    if not state.final_report_required and not state.mutation_count:
        return []
    checklist = [
        "Root cause: explain why the problem happened.",
        "Changes: list the files, configs, or command paths you changed.",
        "Validation: list the commands you actually ran and their results.",
        "Evidence: cite the key tool results, test results, or log facts.",
        "Cleanup: report cleanup status and artifact categorization/classification; LoopCraft only reminds and never deletes files automatically.",
        "Remaining risks: note any unverified points, environment limits, or follow-up advice.",
    ]
    if state.pending_verification:
        checklist.insert(0, "Incomplete: there are still unvalidated changes and they must be verified before the final response.")
    return checklist


def close_summary(state: SessionRuntimeState) -> dict[str, Any]:
    """Build a session-close summary suitable for the audit trail."""
    snapshot = task_snapshot(state)
    snapshot["final_status"] = "unverified" if state.pending_verification else task_status(state)
    snapshot["raw_state"] = asdict(state)
    return snapshot
