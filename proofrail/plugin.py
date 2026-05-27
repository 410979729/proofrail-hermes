"""Hermes hook adapter for the autonomous coding harness.

This module intentionally stays thin: it translates Hermes hook calls into
workflow-state updates, audit events, and context injections. Classification and
persistence details live in smaller helper modules so the hook flow remains easy
to review.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .audit import AuditLogger, default_audit_log_path
from .constants import NEW_BEHAVIOR_RULES, PLUGIN_NAME
from .models import LlmContextResult, PluginSettings
from .path_utils import mutates_existing_path
from .result_status import get_tool_result_status
from .session_state import STATE_STORE, build_tool_intent_signature, record_dangerous_command, record_tool_observation
from .settings import root_dir_from_context, settings_from_context
from .summarize import clamp_summary_threshold, summarize_large_output
from .task_ledger import close_summary, final_review_checklist, render_task_context, task_snapshot
from .text_utils import compact_label, extract_text_from_tool_result
from .tooling import get_exec_command, get_tool_category, is_dangerous_command, is_likely_mutating_exec, is_likely_validation_exec
from .validation import changed_path_hints, suggest_validations

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


class RuntimeHooks:
    """Stateful hook implementation registered by ``register(ctx)``.

    The hooks do not sandbox the agent. They enforce an autonomous engineering
    loop: observe first, mutate with evidence, validate after mutation, and keep
    a JSONL audit trail for later review.
    """

    def __init__(self, settings: PluginSettings | None = None, *, root_dir: str | None = None) -> None:
        self.settings = settings or PluginSettings()
        self.root_dir = root_dir
        audit_path = self.settings.audit_log_path or default_audit_log_path(root_dir)
        self.audit = AuditLogger(audit_path, enabled=self.settings.audit_enabled)

    @property
    def tool_aliases(self) -> dict[str, str]:
        return dict(self.settings.tool_aliases)

    def debug_state(self, session_id: str) -> dict[str, Any]:
        return asdict(STATE_STORE.snapshot(session_id))

    def explain_state(self, session_id: str = "") -> dict[str, Any]:
        """Return a JSON-safe snapshot useful for debug tools and tests."""
        state = STATE_STORE.snapshot(session_id)
        next_expected = "observe"
        if state.pending_verification:
            next_expected = "validation"
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
        """Write the final task audit summary and clear session state once."""
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
        """Preflight a tool call before Hermes executes it."""
        payload = args or {}
        state = STATE_STORE.snapshot(session_id)
        category = get_tool_category(tool_name, self.tool_aliases)
        command = get_exec_command(payload)
        mutating_exec = category == "exec" and is_likely_mutating_exec(command)
        mutation_touches_existing_path = category == "write" and mutates_existing_path(payload, base_dir=self.root_dir)
        is_mutation = category == "write" or mutating_exec
        tool_intent = build_tool_intent_signature(tool_name, payload, self.tool_aliases)

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
            # warn/allow are autonomous modes. They do not ask the user, but
            # they must not bypass the workflow guardrails below. A dangerous
            # command can still be blocked if it is a mutation before evidence or
            # if a previous mutation has not been validated yet.
            if self.settings.dangerous_command_action == "warn":
                self.audit.record("tool_warning", session_id=session_id or "default", tool_name=tool_name, warning=f"dangerous command allowed with audit if workflow checks pass: {label}")
            elif self.settings.dangerous_command_action == "allow":
                self.audit.record("tool_decision", session_id=session_id or "default", tool_name=tool_name, decision={"action": "allow"}, reason="dangerous_command_allow_if_workflow_checks_pass")

        if state.pending_verification and is_mutation:
            return self._blocked(session_id, tool_name, payload, f"Validate the recent change before continuing: {state.last_mutation_label or 'recent mutation'}", reason="pending_verification")

        if state.evidence_count == 0 and (mutating_exec or mutation_touches_existing_path):
            return self._blocked(session_id, tool_name, payload, "Inspect nearby code, config, logs, or tests first. Gather local evidence before editing existing files or changing processes.", reason="missing_evidence")

        if state.consecutive_low_signal >= self.settings.low_signal_block_threshold and state.last_low_signal_intent == tool_intent:
            return self._blocked(session_id, tool_name, payload, "Recent tool calls produced no new facts. Switch paths, keywords, log sources, hosts, or validation methods.", reason="low_signal_repeat")

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
        """Record the observed result of a tool call and advance session state."""
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
        """Summarize large tool outputs before they are returned to the model."""
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
        """Inject workflow rules, task-ledger state, and validation reminders."""
        state = STATE_STORE.snapshot(session_id)
        extra = NEW_BEHAVIOR_RULES
        if state.phase == "observe":
            extra += "\n\n## [PLUGIN STATE] Current phase: Observe\nThere is not enough local evidence yet. Inspect code, config, logs, tests, or health probes on the control path before mutating existing files or processes."
        elif state.phase == "execute":
            suffix = f" (latest: {state.last_evidence_label})" if state.last_evidence_label else ""
            extra += f"\n\n## [PLUGIN STATE] Current phase: Execute\nLocal evidence has been gathered{suffix}. Keep the next change minimal and stay close to the control path."
        elif state.phase == "review":
            extra += f"\n\n## [PLUGIN STATE] Current phase: Review\nA recent change was made ({state.last_mutation_label or 'recent mutation'}). Run the narrowest validation before expanding the change set."
        extra += "\n\n" + render_task_context(state)
        if state.pending_verification:
            extra += f"\n\n## [PLUGIN REMINDER] ⚠️ Validate before continuing\nA file, config, or process change just happened ({state.last_mutation_label or 'recent mutation'}). Run the narrowest validation next instead of stacking more changes."
        if state.validation_suggestions:
            suggestions = "\n".join(f"- {item}" for item in state.validation_suggestions)
            extra += f"\n\n## [PLUGIN REMINDER] Suggested narrow validation\n{suggestions}"
        if state.touched_files:
            touched = "\n".join(f"- {item}" for item in state.touched_files)
            extra += f"\n\n## [PLUGIN STATE] Touched files / paths in this session\n{touched}"
        if state.dangerous_count:
            extra += f"\n\n## [PLUGIN STATE] ⚠️ High-risk command audit\nObserved {state.dangerous_count} high-risk command(s) in this session (latest: {state.last_dangerous_label}). If autonomous execution continues, validate the effects and explain the risk in the final report."
        checklist = final_review_checklist(state)
        if checklist:
            extra += "\n\n## [PLUGIN REMINDER] Final report requirements / checklist\n" + "\n".join(f"- {item}" for item in checklist)
        if state.consecutive_low_signal >= self.settings.low_signal_block_threshold:
            extra += f"\n\n## [PLUGIN REMINDER] ⚠️ Switch probes now\nThe last {state.consecutive_low_signal} tool calls produced no new facts. Do not repeat the same command or search layer unchanged; switch logs, paths, keywords, hosts, sources, or upstream docs."
        return asdict(LlmContextResult(context=extra))

    def _blocked(self, session_id: str, tool_name: str, args: dict[str, Any], message: str, *, reason: str) -> HookDecision:
        decision = _decision("block", message)
        self.audit.record("tool_decision", session_id=session_id or "default", tool_name=tool_name, args=args, decision=decision, reason=reason)
        return decision


def build_runtime_hooks(settings: PluginSettings | None = None, *, root_dir: str | None = None) -> RuntimeHooks:
    """Factory used by tests and Hermes registration."""
    return RuntimeHooks(settings=settings, root_dir=root_dir)


def register(ctx: Any) -> None:
    """Hermes plugin entrypoint. Registers all runtime hooks on the host ctx."""
    hooks = build_runtime_hooks(settings=settings_from_context(ctx), root_dir=root_dir_from_context(ctx))
    ctx.register_hook("on_session_start", hooks.on_session_start)
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.post_tool_call)
    ctx.register_hook("transform_tool_result", hooks.transform_tool_result)
    ctx.register_hook("pre_llm_call", hooks.pre_llm_call)
    ctx.register_hook("on_session_end", hooks.on_session_end)
    ctx.register_hook("on_session_finalize", hooks.on_session_finalize)
