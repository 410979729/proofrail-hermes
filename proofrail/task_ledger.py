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
        "## [PLUGIN STATE] 自主任务账本",
        f"- 状态: {task_status(state)}",
        f"- 证据/改动/验证: {state.evidence_count}/{state.mutation_count}/{state.validation_count}",
    ]
    if state.evidence_labels:
        lines.append("- 最近证据:")
        lines.extend(f"  - {item}" for item in state.evidence_labels[-5:])
    if state.mutation_labels:
        lines.append("- 本轮改动:")
        lines.extend(f"  - {item}" for item in state.mutation_labels[-5:])
    if state.validation_labels:
        lines.append("- 已通过验证:")
        lines.extend(f"  - {item}" for item in state.validation_labels[-5:])
    if state.pending_verification:
        lines.append("- 下一步: 先运行最窄验证，不要继续叠加新改动。")
    elif state.mutation_count and state.validation_count:
        lines.append("- 下一步: 可以继续推进，但每次新增改动后仍要立刻验证。")
    elif state.evidence_count:
        lines.append("- 下一步: 可以做最小可解释改动，随后立刻验证。")
    else:
        lines.append("- 下一步: 先读取最靠近控制路径的代码、配置、日志或测试。")
    return "\n".join(lines)


def final_review_checklist(state: SessionRuntimeState) -> list[str]:
    """Return the final response checklist for sessions with mutations."""
    if not state.final_report_required and not state.mutation_count:
        return []
    checklist = [
        "根因：说明问题为什么发生。",
        "改动：列出改了哪些文件/配置/命令路径。",
        "验证：列出实际运行的验证命令和结果。",
        "证据：引用关键工具结果、测试结果或日志事实。",
        "剩余风险：说明未验证点、环境限制或后续建议。",
    ]
    if state.pending_verification:
        checklist.insert(0, "未完成：当前仍有未验证改动，最终答复前必须先补验证。")
    return checklist


def close_summary(state: SessionRuntimeState) -> dict[str, Any]:
    """Build a session-close summary suitable for the audit trail."""
    snapshot = task_snapshot(state)
    snapshot["final_status"] = "unverified" if state.pending_verification else task_status(state)
    snapshot["raw_state"] = asdict(state)
    return snapshot
