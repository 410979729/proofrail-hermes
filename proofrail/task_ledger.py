"""Task-ledger rendering and summaries for autonomous coding sessions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import SessionRuntimeState


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


def task_snapshot(state: SessionRuntimeState) -> dict[str, Any]:
    """Build a JSON-safe task ledger snapshot from the runtime state."""
    return {
        "status": task_status(state),
        "phase": state.phase,
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


def render_task_context(state: SessionRuntimeState) -> str:
    """Render task-ledger context for pre_llm_call injection."""
    lines = [
        "## [SYSTEM-ADDED PLUGIN STATE — GENERATED, NOT USER-PROVIDED] Autonomous task ledger",
        f"- Status: {task_status(state)}",
        f"- Evidence / mutations / validations: {state.evidence_count}/{state.mutation_count}/{state.validation_count}",
    ]
    if state.evidence_labels:
        lines.append("- Recent evidence:")
        lines.extend(f"  - {item}" for item in state.evidence_labels[-5:])
    if state.mutation_labels:
        lines.append("- Mutations in this session:")
        lines.extend(f"  - {item}" for item in state.mutation_labels[-5:])
    if state.validation_labels:
        lines.append("- Validations passed:")
        lines.extend(f"  - {item}" for item in state.validation_labels[-5:])
    if state.pending_verification:
        lines.append("- Next: run the narrowest validation before adding more changes.")
    elif state.mutation_count and state.validation_count:
        lines.append("- Next: you may continue, but every new mutation still needs immediate validation.")
    elif state.evidence_count:
        lines.append("- Next: make the smallest explainable change, then validate it immediately.")
    else:
        lines.append("- Next: inspect code, config, logs, or tests closest to the control path.")
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
