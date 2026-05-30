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
)
from .settings import root_dir_from_context, settings_from_context
from .summarize import clamp_summary_threshold, summarize_large_output
from .task_ledger import close_summary, final_review_checklist, task_snapshot
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
    """Return True when blocked, pending_verification, low-signal, or classifier warning are active."""
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
    """Short system-status context for clean observation/read-only scenarios."""
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
    return "\n".join(lines)


class RuntimeHooks:
    """Stateful hook implementation registered by ``register(ctx)``.

    The hooks do not sandbox the agent. They enforce an autonomous engineering
    loop: observe first, mutate with evidence, validate after mutation, and keep
    a JSONL audit trail for later review.
    """

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
            "last_classifier_decision": state.last_classifier_decision,
            "last_classifier_reason": state.last_classifier_reason,
            "last_classifier_evidence_gap": state.last_classifier_evidence_gap,
            "last_classifier_guidance": list(state.last_classifier_guidance),
            "last_classifier_source": state.last_classifier_source,
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
            target_hints = list(state.touched_files) or changed_path_hints(tool_name, payload, command)
            target_label = compact_label(target_hints[0], 120) if target_hints else compact_label(state.last_mutation_label or "recent mutation", 120)
            message = (
                "Blocked by Proofrail [pending_verification].\n"
                f"Target: {target_label}\n"
                f"Recommended next step: validate {target_label} directly before attempting another mutation.\n"
                "Accepted validation shapes: read back the touched path directly, or run a narrow validation command against the same touched path/process.\n"
                f"Enough when: you confirm the last mutation landed as intended ({state.last_mutation_label or 'recent mutation'}).\n"
                "Do not: stack another mutation, inspect plugin internals, or search for alternate mutation paths."
            )
            return self._blocked(session_id, tool_name, payload, message, reason="pending_verification")

        if state.evidence_count == 0 and (mutating_exec or mutation_touches_existing_path):
            target_hints = changed_path_hints(tool_name, payload, command)
            target_label = compact_label(target_hints[0], 120) if target_hints else compact_label(state.last_mutation_label or "the target path/process", 120)
            message = (
                "Blocked by Proofrail [missing_evidence].\n"
                f"Target: {target_label}\n"
                f"Recommended next step: directly inspect {target_label} or the closest control-path artifact for this task.\n"
                "One direct check is enough before retrying the mutation.\n"
                "Do not: start with plugin internals, plugin tests/config, or full audit/gateway history."
            )
            return self._blocked(session_id, tool_name, payload, message, reason="missing_evidence")

        if state.consecutive_low_signal >= self.settings.low_signal_block_threshold and state.last_low_signal_intent == tool_intent:
            return self._blocked(session_id, tool_name, payload, "Recent tool calls produced no new facts. Switch paths, keywords, log sources, hosts, or validation methods.", reason="low_signal_repeat")

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
                if decision.decision == "block":
                    guidance = "\n".join(f"- {item}" for item in decision.guidance)
                    message = (
                        "Blocked by Proofrail [llm_classifier].\n"
                        f"Reason: {decision.reason or 'Gray-area mutation rejected by classifier.'}\n"
                        f"Evidence gap: {decision.evidence_gap}"
                    )
                    if guidance:
                        message += f"\nRecommended next step(s):\n{guidance}"
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
        """Inject compact workflow state and next-step reminders."""
        state = STATE_STORE.snapshot(session_id)

        if _has_risk(state, self.settings.low_signal_block_threshold):
            from .task_ledger import task_status

            status = task_status(state)
            extra = (
                "## [SYSTEM STATUS — not user input]\n"
                f"- Phase: {state.phase} | task: {status}"
            )
            if state.phase == "observe":
                extra += "\n- Do not start by reading plugin internals or full audit history."
            elif state.phase == "execute":
                suffix = f" (latest evidence: {state.last_evidence_label})" if state.last_evidence_label else ""
                extra += f"\n- Keep the next change minimal and stay on the same control path.{suffix}"
            elif state.phase == "review":
                extra += (
                    f"\n- A recent change was made ({state.last_mutation_label or 'recent mutation'}). "
                    "Validate before expanding the change set."
                )
            if state.pending_verification:
                extra += (
                    "\n\n## [SYSTEM STATUS — validation required]\n"
                    f"- Validate next: {state.last_mutation_label or 'recent mutation'}\n"
                    "- Do not stack more changes before this validation."
                )
            if state.validation_suggestions:
                suggestions = "\n".join(f"- {item}" for item in state.validation_suggestions)
                extra += f"\n\n## [SYSTEM STATUS — Suggested narrow validation]\n{suggestions}"
            if state.touched_files:
                touched = "\n".join(f"- {item}" for item in state.touched_files)
                extra += f"\n\n## [SYSTEM STATUS — touched paths]\n{touched}"
            if state.dangerous_count:
                extra += (
                    "\n\n## [SYSTEM STATUS — high-risk command audit]\n"
                    f"- Observed: {state.dangerous_count}\n"
                    f"- Latest: {state.last_dangerous_label}\n"
                    "- If execution continues, validate the effects and mention the risk in the final report."
                )
            checklist = final_review_checklist(state)
            if checklist:
                extra += "\n\n## [SYSTEM STATUS — Final report requirements]\n" + "\n".join(f"- {item}" for item in checklist)
            if state.consecutive_low_signal >= self.settings.low_signal_block_threshold:
                extra += (
                    "\n\n## [SYSTEM STATUS — low-signal warning]\n"
                    f"- Recent low-signal count: {state.consecutive_low_signal}\n"
                    "- Switch logs, paths, keywords, hosts, sources, or validation method instead of repeating the same probe."
                )
            if state.last_block_message:
                extra += (
                    "\n\n## [SYSTEM STATUS — last block]\n"
                    "- Last tool call was blocked.\n"
                    f"- Reason: `{state.last_block_reason or 'blocked'}`\n"
                    f"- Message: {state.last_block_message}\n"
                    "- Treat the block message as the required next step, not as an obstacle to route around.\n"
                    "- Do not look for alternate tools, wrapper tools, or equivalent mutations that achieve the same blocked outcome.\n"
                )
                if state.last_block_reason == "pending_verification":
                    extra += (
                        "- Validate the last mutation before any more changes.\n"
                        "- The next step is validation of the touched path/process, not more mutation planning.\n"
                        "- Do not inspect plugin source or search for alternate mutation paths.\n"
                    )
                elif state.last_block_reason == "missing_evidence":
                    extra += (
                        "- Gather local evidence on the same control path before retrying the mutation.\n"
                        "- Prefer one or two direct checks of the target file, path, process, or nearby config snippet.\n"
                        "- Do not read plugin source, plugin tests, plugin config, or full audit/gateway history.\n"
                    )
                elif state.last_block_reason == "low_signal_repeat":
                    extra += (
                        "- Change probe strategy instead of retrying the same intent through another tool.\n"
                        "- Stop broadening the evidence scope after repeated low-signal probes.\n"
                        "- Re-read the last block message and inspect only the immediate target file, path, process, or config snippet.\n"
                        "- Do not read plugin source, plugin tests, plugin config, or full audit/gateway history.\n"
                    )
            if state.last_classifier_decision and state.last_classifier_decision != "allow":
                extra += (
                    "\n\n## [SYSTEM STATUS — LLM classifier review]\n"
                    f"- Decision: `{state.last_classifier_decision}`\n"
                    f"- Evidence gap: `{state.last_classifier_evidence_gap or 'unclear'}`\n"
                    f"- Reason: {state.last_classifier_reason or 'No reason provided.'}"
                )
                if state.last_classifier_guidance:
                    extra += "\n- Guidance:\n" + "\n".join(f"  - {item}" for item in state.last_classifier_guidance)
        else:
            extra = _compact_context(state)

        return asdict(LlmContextResult(context=extra))

    def _blocked(self, session_id: str, tool_name: str, args: dict[str, Any], message: str, *, reason: str) -> HookDecision:
        decision = _decision("block", message)
        record_block_decision(session_id, message, reason)
        self.audit.record("tool_decision", session_id=session_id or "default", tool_name=tool_name, args=args, decision=decision, reason=reason)
        return decision


def build_runtime_hooks(
    settings: PluginSettings | None = None,
    *,
    root_dir: str | None = None,
    classifier: GuardrailClassifier | None = None,
) -> RuntimeHooks:
    """Factory used by tests and Hermes registration."""
    return RuntimeHooks(settings=settings, root_dir=root_dir, classifier=classifier)


def register(ctx: Any) -> None:
    """Hermes plugin entrypoint. Registers all runtime hooks on the host ctx."""
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
