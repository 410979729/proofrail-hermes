"""Hermes hook adapter for the autonomous coding harness.

This module intentionally stays thin: it translates Hermes hook calls into
workflow-state updates, audit events, and context injections. Classification and
persistence details live in smaller helper modules so the hook flow remains easy
to review.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .audit import AuditLogger, default_audit_log_path
from .classifier import (
    GuardrailClassifier,
    GuardrailClassifierDecision,
    HermesLlmGuardrailClassifier,
    RuleBasedGrayAreaClassifier,
    normalize_classifier_decision,
    should_run_classifier,
)
from .constants import PLUGIN_NAME
from .models import LlmContextResult, PluginSettings
from .path_utils import mutates_existing_path
from .result_status import get_tool_result_status
from .session_state import (
    STATE_STORE,
    build_tool_intent_signature,
    clear_classifier_decision,
    record_block_decision,
    record_classifier_decision,
    record_dangerous_command,
    record_tool_observation,
    set_forced_next_mode,
)
from .settings import root_dir_from_context, settings_from_context
from .summarize import clamp_summary_threshold, summarize_large_output
from .task_ledger import close_summary, final_review_checklist, task_snapshot
from .text_utils import compact_label, extract_text_from_tool_result
from .tooling import get_exec_command, get_tool_category, is_dangerous_command, is_likely_mutating_exec, is_likely_validation_exec
from .validation import changed_path_hints, suggest_validations


def _normalize_choice_text(text: str) -> str:
    return " ".join(text.lower().split())


def _looks_like_affirmative_choice(text: str) -> bool:
    normalized = _normalize_choice_text(text)
    if not normalized:
        return False
    return any(token in normalized for token in (
        "yes",
        "yep",
        "yeah",
        "confirm",
        "approved",
        "approve",
        "go ahead",
        "push now",
        "publish now",
        "现在推",
        "可以推",
        "确认",
        "同意",
        "发布",
    ))


def _choice_signature_tokens(text: str) -> tuple[str, ...]:
    normalized = _normalize_choice_text(text)
    if ":" in normalized:
        prefix, rest = normalized.split(":", 1)
        if prefix in {"exec", "terminal", "write", "write_file", "patch"}:
            normalized = rest
    raw_tokens = [token for token in re.split(r"[^a-z0-9_./:-]+", normalized) if token]
    stopwords = {"origin", "command", "file", "path", "branch", "remote", "terminal", "write", "patch"}
    ordered: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        for part in re.split(r"[./:-]+", token):
            if len(part) < 4 or part in stopwords:
                continue
            if part not in seen:
                seen.add(part)
                ordered.append(part)
    return tuple(ordered)


def _signature_matches_user_choice(signature: str, response_text: str) -> bool:
    normalized_signature = _normalize_choice_text(signature)
    normalized_response = _normalize_choice_text(response_text)
    if not normalized_signature or not normalized_response:
        return False
    if normalized_signature in normalized_response:
        return True
    signature_tokens = _choice_signature_tokens(signature)
    response_tokens = set(_choice_signature_tokens(response_text))
    return bool(signature_tokens) and all(token in response_tokens for token in signature_tokens)


logger = logging.getLogger(__name__)
HookDecision = dict[str, str]

_FILE_INSPECTION_EXECUTABLES = {
    "awk",
    "bat",
    "batcat",
    "cat",
    "file",
    "grep",
    "head",
    "less",
    "more",
    "nl",
    "rg",
    "sed",
    "stat",
    "tail",
    "wc",
}
_COMMAND_WRAPPERS = {"command", "env", "sudo", "time"}


def _decision(action: str, message: str) -> HookDecision:
    return {"action": action, "message": message}


def _base_dir_for_tool_call(payload: dict[str, Any], root_dir: str | None) -> str | None:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return root_dir


def _normalize_path_hint(path_hint: str, base_dir: str | None) -> str | None:
    if not path_hint or path_hint.startswith(("http://", "https://")):
        return None
    wrapper_match = re.fullmatch(r"(?:PosixPath|WindowsPath|Path)\((?P<quote>['\"])(?P<inner>.*?)(?P=quote)\)", path_hint)
    if wrapper_match:
        path_hint = wrapper_match.group("inner")
    try:
        path = Path(path_hint).expanduser()
        if not path.is_absolute():
            path = (Path(base_dir).expanduser() if base_dir else Path.cwd()) / path
        return str(path.resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return None


def _path_hints_overlap(left: tuple[str, ...] | list[str], right: tuple[str, ...] | list[str], base_dir: str | None) -> bool:
    if not left or not right:
        return False
    left_paths = {_normalize_path_hint(path, base_dir) for path in left}
    right_paths = {_normalize_path_hint(path, base_dir) for path in right}
    left_paths.discard(None)
    right_paths.discard(None)
    return bool(left_paths & right_paths)


def _exec_program(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    while parts:
        program = Path(parts.pop(0)).name
        if program == "env":
            while parts and "=" in parts[0] and not parts[0].startswith("-"):
                parts.pop(0)
            continue
        if program in _COMMAND_WRAPPERS:
            continue
        return program
    return ""


def _is_file_readback_call(category: str, command: str, mutating_exec: bool) -> bool:
    if category == "read":
        return True
    if category != "exec" or mutating_exec or not command:
        return False
    return _exec_program(command) in _FILE_INSPECTION_EXECUTABLES


def _readback_validates_touched_file(
    *,
    category: str,
    payload: dict[str, Any],
    command: str,
    mutating_exec: bool,
    touched_files: tuple[str, ...],
    read_paths: list[str],
    root_dir: str | None,
) -> bool:
    if not _is_file_readback_call(category, command, mutating_exec):
        return False
    base_dir = _base_dir_for_tool_call(payload, root_dir)
    return _path_hints_overlap(touched_files, read_paths, base_dir)


def _has_risk(state, low_signal_threshold: int) -> bool:
    if state.pending_verification:
        return True
    if state.last_block_message:
        return True
    if state.consecutive_low_signal >= low_signal_threshold:
        return True
    if state.last_classifier_decision and state.last_classifier_decision != "allow":
        return True
    return False


def _compact_context(state) -> str:
    from .task_ledger import task_status

    status = task_status(state)
    lines = [
        "## [SYSTEM STATUS — not user input]",
        f"- Phase: {state.phase} | task: {status}",
    ]
    if state.evidence_count == 0:
        lines.append("- Next: inspect the closest code, config, log, or test on the control path.")
    elif state.mutation_count == 0:
        lines.append("- Next: make the smallest explainable change, then validate it immediately.")
    else:
        lines.append("- Next: report root cause, changes, validation, evidence, and remaining risks.")
    if state.forced_next_exit_condition == "validation complete" and state.forced_next_why:
        lines.append(f"- {state.forced_next_exit_condition}")
        lines.append(f"- {state.forced_next_why}")
    return "\n".join(lines)


def _mode_specific_handoff_line(state, target: str) -> str:
    if state.forced_next_mode == "gather_target_evidence":
        return f"This is an evidence handoff: inspect {target} or the closest control-path artifact before mutating."
    if state.forced_next_mode == "validate_only":
        return "This is a verification handoff: prove the last change landed before any more mutation."
    if state.forced_next_mode == "change_strategy":
        return "This is a strategy handoff: switch probe shape once without broadening scope."
    if state.forced_next_mode == "user_choice":
        return "This is a decision handoff: wait for an explicit user choice before mutating."
    return "This is the current cooperative handoff; complete it directly to reopen forward progress."


def _subgoal_for_mode(state, target: str) -> str:
    if state.forced_next_mode == "gather_target_evidence":
        return "inspect the real target before mutating it"
    if state.forced_next_mode == "validate_only":
        return f"verify the last change on {target}"
    if state.forced_next_mode == "change_strategy":
        return "change probe strategy without broadening scope"
    if state.forced_next_mode == "user_choice":
        return "wait for an explicit user decision before continuing"
    return state.last_mutation_label or state.last_evidence_label or "continue the current control-path task"


def _render_block_message(
    *,
    reason: str,
    mode: str,
    target: str,
    why_blocked: str,
    subgoal: str,
    next_actions: list[str] | tuple[str, ...],
    done_when: list[str] | tuple[str, ...],
    avoid: list[str] | tuple[str, ...],
) -> str:
    normalized_next_actions = [str(item).strip() for item in next_actions if str(item or "").strip()]
    normalized_done_when = [str(item).strip() for item in done_when if str(item or "").strip()]
    normalized_avoid = [str(item).strip() for item in avoid if str(item or "").strip()]
    smallest_next_action = normalized_next_actions[0] if normalized_next_actions else "perform the narrowest action that satisfies this handoff"
    lines = [
        f"Blocked by Proofrail [{reason}]",
        f"Proofrail mode switch: {mode}",
        f"Target: {target}",
        f"Why blocked now: {why_blocked}",
        f"Current subgoal: {subgoal}",
        "Recommended next step:",
        *[f"- {item}" for item in normalized_next_actions],
        "- One direct check is enough before continuing",
        "Smallest next action:",
        f"- {smallest_next_action}",
        "Enough when:",
        *[f"- {item}" for item in normalized_done_when],
        "Done when:",
        *[f"- {item}" for item in normalized_done_when],
        "Do not:",
        *[f"- {item}" for item in normalized_avoid],
        "Avoid right now:",
        *[f"- {item}" for item in normalized_avoid],
        "If unsure:",
        "- do the first smallest next action above",
        "- do not infer extra requirements beyond this handoff",
    ]
    return "\n".join(lines)


def _render_task_panel(state) -> str:
    from .task_ledger import final_review_checklist, task_status

    target = state.forced_next_target or (state.touched_files[0] if state.touched_files else state.last_mutation_label or "the current target")
    subgoal = _subgoal_for_mode(state, target)

    lines = [
        "## [PROOFRAIL TASK PANEL — not user input]",
        "task objective: complete the user task with verified progress",
        f"current phase: {state.phase}",
        f"current proofrail mode: {state.forced_next_mode}",
        f"current target: {target}",
        f"current subgoal: {subgoal}",
        f"why this matters: {state.forced_next_why or 'This is the fastest safe way to regain forward progress.'}",
        f"success / exit condition: {state.forced_next_exit_condition or 'satisfy the current Proofrail subgoal directly'}",
        "",
        "smallest next action:",
    ]
    allowed = list(state.allowed_next_actions) or ["perform the narrowest action that satisfies the current subgoal"]
    lines.extend(f"- {item}" for item in allowed)
    lines.append("")
    lines.append("avoid right now:")
    forbidden = list(state.forbidden_next_actions) or ["broad replanning before satisfying the current subgoal"]
    lines.extend(f"- {item}" for item in forbidden)
    lines.extend(
        [
            "",
            "important:",
            f"- {_mode_specific_handoff_line(state, target)}",
            "- This handoff is part of the task, not a refusal.",
            "- The fastest path forward is to satisfy this subgoal directly.",
            "- Complete this subtask to reopen forward progress.",
            "- This mode is a collaboration handoff, not a failure state.",
            "- Choose the first smallest next action if the next move is unclear.",
            "- Do not infer extra requirements beyond this handoff.",
            "- Do not re-plan the whole task unless the mode is change_strategy.",
        ]
    )

    status = task_status(state)
    lines.extend(
        [
            "",
            "## [SYSTEM STATUS — not user input]",
            f"- Phase: {state.phase} | task: {status}",
        ]
    )

    if state.phase == "observe":
        lines.append("- Do not start by reading plugin internals or full audit history.")
    elif state.phase == "execute":
        suffix = f" (latest evidence: {state.last_evidence_label})" if state.last_evidence_label else ""
        lines.append(f"- Keep the next change minimal and stay on the same control path.{suffix}")
    elif state.phase == "review":
        lines.append(
            f"- A recent change was made ({state.last_mutation_label or 'recent mutation'}). Validate before expanding the change set."
        )

    if state.pending_verification:
        lines.extend(
            [
                "",
                "## [SYSTEM STATUS — validation required]",
                f"- Validate next: {state.last_mutation_label or 'recent mutation'}",
                "- Do not stack more changes before this validation.",
            ]
        )

    if state.validation_suggestions:
        lines.extend(["", "## [SYSTEM STATUS — Suggested narrow validation]"])
        lines.extend(f"- {item}" for item in state.validation_suggestions)

    if state.touched_files:
        lines.extend(["", "## [SYSTEM STATUS — touched paths]"])
        lines.extend(f"- {item}" for item in state.touched_files)

    checklist = final_review_checklist(state)
    if checklist:
        lines.extend(["", "## [SYSTEM STATUS — Final report requirements]"])
        lines.extend(f"- {item}" for item in checklist)

    if state.consecutive_low_signal >= 2:
        lines.extend(
            [
                "",
                "## [SYSTEM STATUS — low-signal warning]",
                f"- Recent low-signal count: {state.consecutive_low_signal}",
                "- Switch logs, paths, keywords, hosts, sources, or validation method instead of repeating the same probe.",
            ]
        )

    if state.last_block_message:
        lines.extend(
            [
                "",
                "## [SYSTEM STATUS — last block]",
                "- Last tool call was blocked.",
                f"- Reason: `{state.last_block_reason or 'blocked'}`",
                f"- Message: {state.last_block_message}",
                "- Treat the block message as the required next step, not as an obstacle to route around.",
                "- Do not look for alternate tools, wrapper tools, or equivalent mutations that achieve the same blocked outcome.",
            ]
        )
        if state.last_block_reason == "pending_verification":
            lines.extend(
                [
                    "- Validate the last mutation before any more changes.",
                    "- The next step is validation of the touched path/process, not more mutation planning.",
                    "- Do not inspect plugin source or search for alternate mutation paths.",
                ]
            )
        elif state.last_block_reason == "missing_evidence":
            lines.extend(
                [
                    "- Gather local evidence on the same control path before retrying the mutation.",
                    "- Prefer one or two direct checks of the target file, path, process, or nearby config snippet.",
                    "- Do not read plugin source, plugin tests, plugin config, or full audit/gateway history.",
                ]
            )
        elif state.last_block_reason == "low_signal_repeat":
            lines.extend(
                [
                    "- Change probe strategy instead of retrying the same intent through another tool.",
                    "- Stop broadening the evidence scope after repeated low-signal probes.",
                    "- Re-read the last block message and inspect only the immediate target file, path, process, or config snippet.",
                    "- Do not read plugin source, plugin tests, plugin config, or full audit/gateway history.",
                ]
            )

    if state.last_classifier_decision and state.last_classifier_decision != "allow":
        lines.extend(
            [
                "",
                "## [SYSTEM STATUS — LLM classifier review]",
                f"- Decision: `{state.last_classifier_decision}`",
                f"- Evidence gap: `{state.last_classifier_evidence_gap or 'unclear'}`",
                f"- Reason: {state.last_classifier_reason or 'No reason provided.'}",
            ]
        )
        if state.last_classifier_guidance:
            lines.append("- Guidance:")
            lines.extend(f"  - {item}" for item in state.last_classifier_guidance)

    return "\n".join(lines)


def _apply_classifier_mode(session_id: str, decision: GuardrailClassifierDecision, target_label: str, audit: AuditLogger | None = None) -> None:
    previous_state = STATE_STORE.snapshot(session_id)
    if decision.evidence_gap == "strategy_shift":
        set_forced_next_mode(
            session_id,
            mode="change_strategy",
            target=target_label,
            why=decision.reason or "Switch strategy without broadening scope.",
            exit_condition="make one different target-local probe that yields a new fact",
            allowed_actions=decision.guidance or (
                "switch to a different target-local probe shape",
                "stay on the same target",
            ),
            forbidden_actions=(
                "repeat the same probe shape",
                "broaden scope",
                "re-plan the whole task",
            ),
        )
        if audit is not None:
            audit.record(
                "forced_mode_transition",
                session_id=session_id or "default",
                previous_mode=previous_state.forced_next_mode,
                mode="change_strategy",
                target=target_label,
                reason=decision.reason,
                guidance=list(decision.guidance),
                source=decision.source,
                cleared=False,
            )
    elif decision.evidence_gap == "target_state":
        set_forced_next_mode(
            session_id,
            mode="gather_target_evidence",
            target=target_label,
            why=decision.reason or "Inspect the target directly before mutating it.",
            exit_condition="obtain one concrete local fact about the target state",
            allowed_actions=decision.guidance or (
                f"inspect {target_label} directly",
                f"inspect the closest config, log, or test on the same control path as {target_label}",
            ),
            forbidden_actions=(
                "mutate before direct target evidence",
                "route around the handoff with an equivalent mutation",
                "broad search or plugin internals",
            ),
        )
        if audit is not None:
            audit.record(
                "forced_mode_transition",
                session_id=session_id or "default",
                previous_mode=previous_state.forced_next_mode,
                mode="gather_target_evidence",
                target=target_label,
                reason=decision.reason,
                guidance=list(decision.guidance),
                source=decision.source,
                cleared=False,
            )
    elif decision.evidence_gap in {"change_readback", "narrow_validation"}:
        set_forced_next_mode(
            session_id,
            mode="validate_only",
            target=target_label,
            why=decision.reason or "Run one narrow validation before more mutation.",
            exit_condition=f"confirm the relevant state on {target_label} with one narrow validation",
            allowed_actions=decision.guidance or (
                f"read back {target_label} directly",
                f"run one narrow validation against {target_label}",
            ),
            forbidden_actions=(
                "further mutation",
                "alternate tools for the same mutation",
                "broad search or replanning",
            ),
        )
        if audit is not None:
            audit.record(
                "forced_mode_transition",
                session_id=session_id or "default",
                previous_mode=previous_state.forced_next_mode,
                mode="validate_only",
                target=target_label,
                reason=decision.reason,
                guidance=list(decision.guidance),
                source=decision.source,
                cleared=False,
            )
    elif decision.decision == "ask_user" or decision.evidence_gap == "user_choice":
        set_forced_next_mode(
            session_id,
            mode="user_choice",
            target=target_label,
            why=decision.reason or "A real user preference determines the correct next step.",
            exit_condition="receive an explicit user decision that selects one mutation path",
            allowed_actions=decision.guidance or (
                "ask the user to choose between the valid options",
            ),
            forbidden_actions=(
                "mutate before the choice is explicit",
                "guess the user's preference",
            ),
        )
        if audit is not None:
            audit.record(
                "forced_mode_transition",
                session_id=session_id or "default",
                previous_mode=previous_state.forced_next_mode,
                mode="user_choice",
                target=target_label,
                reason=decision.reason,
                guidance=list(decision.guidance),
                source=decision.source,
                cleared=False,
            )


def _audit_tool_observation_mode_transition(
    audit: AuditLogger,
    *,
    session_id: str,
    previous_state,
    current_state,
    reason: str,
) -> None:
    if previous_state.forced_next_mode == current_state.forced_next_mode and previous_state.forced_next_target == current_state.forced_next_target:
        return
    target = current_state.forced_next_target or previous_state.forced_next_target
    if not target:
        touched = list(current_state.touched_files) or list(previous_state.touched_files)
        target = touched[0] if touched else None
    audit.record(
        "forced_mode_transition",
        session_id=session_id or "default",
        previous_mode=previous_state.forced_next_mode,
        mode=current_state.forced_next_mode,
        target=target,
        reason=reason,
        guidance=list(current_state.allowed_next_actions),
        source="tool_observation",
        cleared=current_state.forced_next_mode == "none",
        exit_condition=current_state.forced_next_exit_condition or previous_state.forced_next_exit_condition,
        why=current_state.forced_next_why or previous_state.forced_next_why,
    )


def _audit_block_mode_transition(
    audit: AuditLogger,
    *,
    session_id: str,
    previous_mode: str,
    current_state,
    reason: str,
) -> None:
    audit.record(
        "forced_mode_transition",
        session_id=session_id or "default",
        previous_mode=previous_mode,
        mode=current_state.forced_next_mode,
        target=current_state.forced_next_target,
        reason=reason,
        guidance=list(current_state.allowed_next_actions),
        source="tool_block",
        cleared=False,
        exit_condition=current_state.forced_next_exit_condition,
        why=current_state.forced_next_why,
    )


class RuntimeHooks:
    """Stateful hook implementation registered by ``register(ctx)``."""

    def __init__(
        self,
        settings: PluginSettings | None = None,
        *,
        root_dir: str | None = None,
        classifier: GuardrailClassifier | None = None,
    ) -> None:
        self.settings = settings or PluginSettings()
        self.root_dir = root_dir
        self.classifier = classifier
        audit_path = self.settings.audit_log_path or default_audit_log_path(root_dir)
        self.audit = AuditLogger(audit_path, enabled=self.settings.audit_enabled)

    @property
    def tool_aliases(self) -> dict[str, str]:
        return dict(self.settings.tool_aliases)

    def debug_state(self, session_id: str) -> dict[str, Any]:
        return asdict(STATE_STORE.snapshot(session_id))

    def explain_state(self, session_id: str = "") -> dict[str, Any]:
        state = STATE_STORE.snapshot(session_id)
        next_expected = "observe"
        if state.forced_next_mode == "validate_only":
            next_expected = "validation"
        elif state.forced_next_mode == "gather_target_evidence":
            next_expected = "target-local evidence"
        elif state.phase == "execute":
            next_expected = "minimal mutation or more evidence"
        elif state.consecutive_low_signal >= self.settings.low_signal_block_threshold:
            next_expected = "change probe strategy"
        return {
            "plugin": PLUGIN_NAME,
            "phase": state.phase,
            "evidence_count": state.evidence_count,
            "pending_verification": state.pending_verification,
            "last_evidence_label": state.last_evidence_label,
            "last_mutation_label": state.last_mutation_label,
            "consecutive_low_signal": state.consecutive_low_signal,
            "last_low_signal_intent": state.last_low_signal_intent,
            "mutation_count": state.mutation_count,
            "validation_count": state.validation_count,
            "dangerous_count": state.dangerous_count,
            "last_dangerous_label": state.last_dangerous_label,
            "last_validation_label": state.last_validation_label,
            "touched_files": list(state.touched_files),
            "validation_suggestions": list(state.validation_suggestions),
            "evidence_labels": list(state.evidence_labels),
            "mutation_labels": list(state.mutation_labels),
            "validation_labels": list(state.validation_labels),
            "dangerous_labels": list(state.dangerous_labels),
            "final_report_required": state.final_report_required,
            "forced_next_mode": state.forced_next_mode,
            "forced_next_target": state.forced_next_target,
            "forced_next_why": state.forced_next_why,
            "forced_next_exit_condition": state.forced_next_exit_condition,
            "allowed_next_actions": list(state.allowed_next_actions),
            "forbidden_next_actions": list(state.forbidden_next_actions),
            "last_classifier_decision": state.last_classifier_decision,
            "last_classifier_reason": state.last_classifier_reason,
            "last_classifier_evidence_gap": state.last_classifier_evidence_gap,
            "last_classifier_guidance": list(state.last_classifier_guidance),
            "last_classifier_source": state.last_classifier_source,
            "pending_user_choice_signature": state.pending_user_choice_signature,
            "approved_mutation_signature": state.approved_mutation_signature,
            "task": task_snapshot(state),
            "next_expected": next_expected,
            "audit_log_path": str(self.audit.path) if self.audit.path else None,
        }

    def on_session_start(self, session_id: str = "", **_: Any) -> None:
        STATE_STORE.snapshot(session_id)
        self.audit.record("session_start", session_id=session_id or "default")

    def on_session_end(self, session_id: str = "", **_: Any) -> None:
        self._close_session("session_end", session_id)

    def on_session_finalize(self, session_id: str = "", **_: Any) -> None:
        self._close_session("session_finalize", session_id)

    def _close_session(self, event: str, session_id: str = "") -> None:
        state = STATE_STORE.peek(session_id)
        if state is None:
            self.audit.record(event, session_id=session_id or "default", already_closed=True)
            return
        summary = close_summary(state)
        self.audit.record(
            event,
            session_id=session_id or "default",
            phase=state.phase,
            pending_verification=state.pending_verification,
            mutation_count=state.mutation_count,
            validation_count=state.validation_count,
            warning="unverified_mutations" if state.pending_verification else None,
            task=summary,
        )
        self.audit.record("task_summary", session_id=session_id or "default", **summary)
        STATE_STORE.clear(session_id)

    def pre_tool_call(self, tool_name: str = "", args: dict[str, Any] | None = None, session_id: str = "", **_: Any) -> HookDecision | None:
        payload = args or {}
        state = STATE_STORE.snapshot(session_id)
        category = get_tool_category(tool_name, self.tool_aliases)
        command = get_exec_command(payload)
        mutating_exec = category == "exec" and is_likely_mutating_exec(command)
        mutation_touches_existing_path = category == "write" and mutates_existing_path(payload, base_dir=self.root_dir)
        is_mutation = category == "write" or mutating_exec
        tool_intent = build_tool_intent_signature(tool_name, payload, self.tool_aliases)
        target_hints = list(state.touched_files) or changed_path_hints(tool_name, payload, command)
        target_label = compact_label(target_hints[0], 120) if target_hints else compact_label(state.last_mutation_label or "the current target", 120)

        approved_user_choice = bool(
            state.approved_mutation_signature
            and is_mutation
            and tool_intent == state.approved_mutation_signature
        )

        dangerous, label = is_dangerous_command(command) if category == "exec" and command else (False, None)
        if dangerous and label:
            record_dangerous_command(session_id, label)
            logger.warning("[%s] dangerous command observed: %s", PLUGIN_NAME, label)
            self.audit.record(
                "dangerous_command",
                session_id=session_id or "default",
                tool_name=tool_name,
                command=command,
                label=label,
                policy=self.settings.dangerous_command_action,
            )
            if self.settings.dangerous_command_action == "block":
                return self._blocked(session_id, tool_name, payload, f"High-risk command blocked by plugin policy: {label}", reason="dangerous_command")
            if self.settings.dangerous_command_action == "approve":
                return self._blocked(
                    session_id,
                    tool_name,
                    payload,
                    f"High-risk command requires manual confirmation before retry: {label}",
                    reason="dangerous_command_approve",
                )
            if self.settings.dangerous_command_action == "warn":
                self.audit.record("tool_warning", session_id=session_id or "default", tool_name=tool_name, warning=f"dangerous command allowed with audit if workflow checks pass: {label}")
            elif self.settings.dangerous_command_action == "allow":
                self.audit.record("tool_decision", session_id=session_id or "default", tool_name=tool_name, decision={"action": "allow"}, reason="dangerous_command_allow_if_workflow_checks_pass")

        if approved_user_choice:
            def _consume_user_choice(current) -> None:
                current.approved_mutation_signature = None
                current.pending_user_choice_signature = None
                current.last_block_message = None
                current.last_block_reason = None
                current.last_classifier_decision = None
                current.last_classifier_reason = None
                current.last_classifier_evidence_gap = None
                current.last_classifier_guidance = ()
                current.last_classifier_source = None
                current.forced_next_mode = "none"
                current.forced_next_target = None
                current.forced_next_why = None
                current.forced_next_exit_condition = None
                current.allowed_next_actions = ()
                current.forbidden_next_actions = ()

            STATE_STORE.update(session_id, _consume_user_choice)
            self.audit.record(
                "user_choice_consumed",
                session_id=session_id or "default",
                tool_name=tool_name,
                command=command,
                approved_signature=tool_intent,
            )
            self.audit.record(
                "tool_preflight",
                session_id=session_id or "default",
                tool_name=tool_name,
                category=category,
                command=command,
                is_mutation=is_mutation,
                decision="allow",
                reason="approved_user_choice",
            )
            return None

        if state.forced_next_mode == "validate_only":
            allowed_readback = _readback_validates_touched_file(
                category=category,
                payload=payload,
                command=command,
                mutating_exec=mutating_exec,
                touched_files=state.touched_files,
                read_paths=changed_path_hints(tool_name, payload, command),
                root_dir=self.root_dir,
            )
            allowed_validation = category == "exec" and (not mutating_exec) and is_likely_validation_exec(command)
            if is_mutation or (not allowed_readback and not allowed_validation):
                message = _render_block_message(
                    reason="pending_verification",
                    mode="validate_only",
                    target=target_label,
                    why_blocked=f"the last change on {target_label} is not yet verified",
                    subgoal=f"verify the last change on {target_label}",
                    next_actions=(
                        "read back the touched path directly",
                        "run one narrow validation command against the same touched path/process",
                        f"read back {target_label} directly",
                        f"run one narrow validation against {target_label}",
                    ),
                    done_when=(f"confirm the last change on {target_label} landed as intended",),
                    avoid=(
                        "further mutation",
                        "alternate tools for the same mutation",
                        "broad search or replanning",
                        "plugin internals or alternate mutation paths",
                    ),
                )
                return self._blocked(session_id, tool_name, payload, message, reason="pending_verification")

        if state.evidence_count == 0 and (mutating_exec or mutation_touches_existing_path):
            target_hints = changed_path_hints(tool_name, payload, command)
            target_label = compact_label(target_hints[0], 120) if target_hints else compact_label(state.last_mutation_label or "the target path/process", 120)
            previous_mode = state.forced_next_mode
            set_forced_next_mode(
                session_id,
                mode="gather_target_evidence",
                target=target_label,
                why="Target-local evidence prevents blind edits and reduces rework.",
                exit_condition="obtain one concrete local fact about the target state",
                allowed_actions=(
                    f"inspect {target_label} directly",
                    f"inspect the closest config, log, or test on the same control path as {target_label}",
                ),
                forbidden_actions=(
                    "start with plugin internals",
                    "read plugin tests/config",
                    "broad search or full audit/gateway history",
                ),
            )
            message = _render_block_message(
                reason="missing_evidence",
                mode="gather_target_evidence",
                target=target_label,
                why_blocked=f"no target-local evidence exists yet for {target_label}",
                subgoal="inspect the real target before mutating it",
                next_actions=(
                    f"directly inspect {target_label}",
                    "inspect the closest control-path artifact",
                    f"inspect {target_label} directly",
                    f"inspect the closest config, log, or test on the same control path as {target_label}",
                ),
                done_when=("obtain one concrete local fact about the target state",),
                avoid=(
                    "plugin internals, plugin tests, or plugin config",
                    "broad search or full audit/gateway history",
                    "broaden scope before the first direct check",
                ),
            )
            payload["_proofrail_previous_mode"] = previous_mode
            return self._blocked(session_id, tool_name, payload, message, reason="missing_evidence")

        if state.consecutive_low_signal >= self.settings.low_signal_block_threshold and state.last_low_signal_intent == tool_intent:
            previous_mode = state.forced_next_mode
            set_forced_next_mode(
                session_id,
                mode="change_strategy",
                target=target_label,
                why="This is a request for one different probe shape, not more investigation volume.",
                exit_condition="make one different target-local probe that yields a new fact",
                allowed_actions=("switch the probe shape once while staying on the same target",),
                forbidden_actions=("repeat the same probe", "broaden scope", "re-plan the whole task"),
            )
            payload["_proofrail_previous_mode"] = previous_mode
            return self._blocked(
                session_id,
                tool_name,
                payload,
                _render_block_message(
                    reason="low_signal_repeat",
                    mode="change_strategy",
                    target=target_label,
                    why_blocked="recent probes repeated the same intent without producing new facts; switch paths, keywords, logs, hosts, sources, or validation method once",
                    subgoal=f"switch probe shape once while staying on {target_label}",
                    next_actions=(
                        f"Switch paths or probe shape once while staying on {target_label}",
                        f"make one different target-local probe against {target_label}",
                        f"inspect the immediate file, path, process, or config snippet for {target_label}",
                    ),
                    done_when=("obtain one new fact from a different probe shape",),
                    avoid=(
                        "repeat the same probe through another tool",
                        "broaden scope",
                        "re-plan the whole task",
                        "plugin internals or full audit/gateway history",
                    ),
                ),
                reason="low_signal_repeat",
            )

        clear_classifier_decision(session_id)
        classifier = self.classifier
        if classifier is None and self.settings.llm_classifier_enabled:
            classifier = RuleBasedGrayAreaClassifier()
        if classifier is not None and should_run_classifier(
            session_state=state,
            category=category,
            is_mutation=is_mutation,
            mutating_exec=mutating_exec,
            mutation_touches_existing_path=mutation_touches_existing_path,
        ):
            decision = normalize_classifier_decision(
                classifier(
                    tool_name=tool_name,
                    args=payload,
                    session_state=state,
                    command=command,
                    category=category,
                    is_mutation=is_mutation,
                )
            )
            if decision is not None:
                _apply_classifier_mode(session_id, decision, target_label, self.audit)
                record_classifier_decision(
                    session_id,
                    decision=decision.decision,
                    reason=decision.reason,
                    evidence_gap=decision.evidence_gap,
                    guidance=decision.guidance,
                    source=decision.source,
                )
                self.audit.record(
                    "classifier_decision",
                    session_id=session_id or "default",
                    tool_name=tool_name,
                    decision=decision.decision,
                    evidence_gap=decision.evidence_gap,
                    source=decision.source,
                    reason=decision.reason,
                    guidance=list(decision.guidance),
                )
                if decision.decision in {"block", "ask_user"}:
                    if decision.decision == "ask_user" or decision.evidence_gap == "user_choice":
                        def _remember_pending_choice(current) -> None:
                            if current.approved_mutation_signature is None:
                                current.pending_user_choice_signature = tool_intent
                            elif current.pending_user_choice_signature is None:
                                current.pending_user_choice_signature = current.approved_mutation_signature

                        STATE_STORE.update(session_id, _remember_pending_choice)
                    classifier_state = STATE_STORE.snapshot(session_id)
                    classifier_target = classifier_state.forced_next_target or target_label
                    guidance_items = tuple(decision.guidance) if decision.guidance else (
                        f"follow the current Proofrail handoff for {classifier_target}",
                    )
                    done_when = (
                        classifier_state.forced_next_exit_condition
                        or "satisfy the classifier-requested handoff before retrying the mutation"
                    )
                    message = _render_block_message(
                        reason="llm_classifier",
                        mode=classifier_state.forced_next_mode,
                        target=classifier_target,
                        why_blocked=decision.reason or "the classifier found a real ambiguity in the requested mutation",
                        subgoal=_subgoal_for_mode(classifier_state, classifier_target),
                        next_actions=guidance_items,
                        done_when=(done_when,),
                        avoid=tuple(classifier_state.forbidden_next_actions) or (
                            "guess the missing user preference",
                            "route around the handoff with an equivalent mutation",
                        ),
                    )
                    return self._blocked(session_id, tool_name, payload, message, reason="llm_classifier")

        self.audit.record(
            "tool_preflight",
            session_id=session_id or "default",
            tool_name=tool_name,
            category=category,
            command=command,
            is_mutation=is_mutation,
            decision="allow",
        )
        return None

    def post_tool_call(
        self,
        tool_name: str = "",
        args: dict[str, Any] | None = None,
        result: Any = None,
        session_id: str = "",
        **_: Any,
    ) -> None:
        payload = args or {}
        category = get_tool_category(tool_name, self.tool_aliases)
        command = get_exec_command(payload)
        mutating_exec = category == "exec" and is_likely_mutating_exec(command)
        validating_exec = category == "exec" and (not mutating_exec) and is_likely_validation_exec(command)
        text = extract_text_from_tool_result(result)
        status = get_tool_result_status(result)
        error_text = "" if status != "failure" else text
        touched_paths = changed_path_hints(tool_name, payload, command)
        prior_state = STATE_STORE.snapshot(session_id)
        readback_validation_succeeded = (
            prior_state.pending_verification
            and status != "failure"
            and _readback_validates_touched_file(
                category=category,
                payload=payload,
                command=command,
                mutating_exec=mutating_exec,
                touched_files=prior_state.touched_files,
                read_paths=touched_paths,
                root_dir=self.root_dir,
            )
        )
        validation_succeeded = (validating_exec and status == "success") or readback_validation_succeeded
        validation_suggestions = suggest_validations(tool_name=tool_name, args=payload, command=command, mutating_exec=mutating_exec)
        state = record_tool_observation(
            session_id=session_id,
            tool_name=tool_name,
            args=payload,
            text=text,
            error_text=error_text,
            mutating_exec=mutating_exec,
            validation_succeeded=validation_succeeded,
            tool_aliases=self.tool_aliases,
            touched_paths=touched_paths,
            validation_suggestions=validation_suggestions,
        )
        if tool_name == "clarify" and prior_state.pending_user_choice_signature and not error_text.strip() and _looks_like_affirmative_choice(text):
            def _approve_user_choice(current) -> None:
                current.approved_mutation_signature = prior_state.pending_user_choice_signature
                current.pending_user_choice_signature = None

            state = STATE_STORE.update(session_id, _approve_user_choice)
            self.audit.record(
                "user_choice_approved",
                session_id=session_id or "default",
                approved_signature=state.approved_mutation_signature,
                response_preview=compact_label(text, 200),
            )
        if state.forced_next_mode != prior_state.forced_next_mode or state.forced_next_target != prior_state.forced_next_target:
            if state.forced_next_mode == "validate_only" and (category == "write" or mutating_exec):
                transition_reason = "pending_verification"
            elif prior_state.forced_next_mode == "validate_only" and state.forced_next_mode == "none" and validation_succeeded:
                transition_reason = "validation_complete"
            else:
                transition_reason = "tool_observation"
            _audit_tool_observation_mode_transition(
                self.audit,
                session_id=session_id,
                previous_state=prior_state,
                current_state=state,
                reason=transition_reason,
            )
            if transition_reason == "validation_complete":
                self.audit.record(
                    "forward_progress_reopened",
                    session_id=session_id or "default",
                    trigger="validation_complete",
                    target=prior_state.forced_next_target or state.last_validation_label,
                    from_mode=prior_state.forced_next_mode,
                    phase=state.phase,
                )
        self.audit.record(
            "tool_result",
            session_id=session_id or "default",
            tool_name=tool_name,
            category=category,
            command=command,
            status=status,
            text_preview=compact_label(text, 500),
            mutating_exec=mutating_exec,
            validating_exec=validating_exec,
            readback_validation_succeeded=readback_validation_succeeded,
            validation_succeeded=validation_succeeded,
            phase=state.phase,
            pending_verification=state.pending_verification,
            touched_paths=touched_paths,
            validation_suggestions=validation_suggestions,
        )

    def transform_tool_result(
        self,
        tool_name: str = "",
        args: dict[str, Any] | None = None,
        result: Any = None,
        **_: Any,
    ) -> str | None:
        text = extract_text_from_tool_result(result)
        if not text:
            return None
        threshold = clamp_summary_threshold(self.settings.summary_threshold_chars)
        summarized = summarize_large_output(text, threshold)
        if summarized != text:
            self.audit.record("tool_result_summarized", tool_name=tool_name, threshold=threshold, original_chars=len(text), summarized_chars=len(summarized))
            return summarized
        return None

    def pre_llm_call(self, session_id: str = "", **_: Any) -> dict[str, str]:
        state = STATE_STORE.snapshot(session_id)
        if state.forced_next_mode != "none":
            extra = _render_task_panel(state)
        elif _has_risk(state, self.settings.low_signal_block_threshold):
            extra = _render_task_panel(state)
        else:
            extra = _compact_context(state)
        return asdict(LlmContextResult(context=extra))

    def _blocked(self, session_id: str, tool_name: str, args: dict[str, Any], message: str, *, reason: str) -> HookDecision:
        decision = _decision("block", message)
        previous_mode = STATE_STORE.snapshot(session_id).forced_next_mode
        previous_mode_hint = args.pop("_proofrail_previous_mode", None) if isinstance(args, dict) else None
        if isinstance(previous_mode_hint, str):
            previous_mode = previous_mode_hint
        record_block_decision(session_id, message, reason)
        current_state = STATE_STORE.snapshot(session_id)
        if current_state.forced_next_mode != previous_mode:
            _audit_block_mode_transition(
                self.audit,
                session_id=session_id,
                previous_mode=previous_mode,
                current_state=current_state,
                reason=reason,
            )
        self.audit.record("tool_decision", session_id=session_id or "default", tool_name=tool_name, args=args, decision=decision, reason=reason)
        return decision


def build_runtime_hooks(
    settings: PluginSettings | None = None,
    *,
    root_dir: str | None = None,
    classifier: GuardrailClassifier | None = None,
) -> RuntimeHooks:
    return RuntimeHooks(settings=settings, root_dir=root_dir, classifier=classifier)


def register(ctx: Any) -> None:
    settings = settings_from_context(ctx)
    classifier: GuardrailClassifier | None = None
    if settings.llm_classifier_enabled and getattr(ctx, "llm", None) is not None:
        classifier = HermesLlmGuardrailClassifier(
            llm=ctx.llm,
            provider=settings.llm_classifier_provider,
            model=settings.llm_classifier_model,
        )
    hooks = build_runtime_hooks(
        settings=settings,
        root_dir=root_dir_from_context(ctx),
        classifier=classifier,
    )
    ctx.register_hook("on_session_start", hooks.on_session_start)
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.post_tool_call)
    ctx.register_hook("transform_tool_result", hooks.transform_tool_result)
    ctx.register_hook("pre_llm_call", hooks.pre_llm_call)
    ctx.register_hook("on_session_end", hooks.on_session_end)
    ctx.register_hook("on_session_finalize", hooks.on_session_finalize)
