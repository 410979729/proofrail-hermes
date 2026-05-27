"""Hermes hook adapter for the autonomous coding harness.

This module intentionally stays thin: it translates Hermes hook calls into
workflow-state updates, audit events, and context injections. Classification and
persistence details live in smaller helper modules so the hook flow remains easy
to review.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
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


def _decision(action: str, message: str) -> HookDecision:
    return {"action": action, "message": message}


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
                return self._blocked(session_id, tool_name, payload, f"检测到高风险命令，已按插件策略直接阻止：{label}", reason="dangerous_command")
            if self.settings.dangerous_command_action == "approve":
                return self._blocked(
                    session_id,
                    tool_name,
                    payload,
                    f"检测到高风险命令，需要人工确认后手动重试：{label}",
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
            return self._blocked(session_id, tool_name, payload, f"先验证刚才的改动：{state.last_mutation_label or 'recent mutation'}", reason="pending_verification")

        if state.evidence_count == 0 and (mutating_exec or mutation_touches_existing_path):
            return self._blocked(session_id, tool_name, payload, "先读取附近代码、配置、日志或测试，拿到现场证据后再改已有文件或启停进程。", reason="missing_evidence")

        if state.consecutive_low_signal >= self.settings.low_signal_block_threshold and state.last_low_signal_intent == tool_intent:
            return self._blocked(session_id, tool_name, payload, "最近连续多次工具调用没有带来新事实。请换路径、关键词、日志源、宿主或验证方式。", reason="low_signal_repeat")

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
        validation_succeeded = validating_exec and status == "success"
        touched_paths = changed_path_hints(tool_name, payload, command)
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
            extra += "\n\n## [PLUGIN STATE] 当前阶段: Observe\n还没有拿到足够的现场证据。先读控制路径附近的代码、配置、日志、测试或健康探针；对已有文件和进程的直接变更会被拦截。"
        elif state.phase == "execute":
            suffix = f"（最近一次: {state.last_evidence_label}）" if state.last_evidence_label else ""
            extra += f"\n\n## [PLUGIN STATE] 当前阶段: Execute\n已取得现场证据{suffix}。继续沿控制路径做最小改动，不要跳步扩大修改面。"
        elif state.phase == "review":
            extra += f"\n\n## [PLUGIN STATE] 当前阶段: Review\n最近刚发生过变更（{state.last_mutation_label or 'recent mutation'}）。先做最窄验证，再继续扩大修改面。"
        extra += "\n\n" + render_task_context(state)
        if state.pending_verification:
            extra += f"\n\n## [PLUGIN REMINDER] ⚠️ 先验证再继续\n刚刚发生过文件/配置/进程变更（{state.last_mutation_label or 'recent mutation'}）。下一步优先做最窄验证，不要继续堆改动。"
        if state.validation_suggestions:
            suggestions = "\n".join(f"- {item}" for item in state.validation_suggestions)
            extra += f"\n\n## [PLUGIN REMINDER] 建议的最窄验证\n{suggestions}"
        if state.touched_files:
            touched = "\n".join(f"- {item}" for item in state.touched_files)
            extra += f"\n\n## [PLUGIN STATE] 本轮已触碰文件/路径\n{touched}"
        if state.dangerous_count:
            extra += f"\n\n## [PLUGIN STATE] ⚠️ 高风险动作审计\n本轮已观察到 {state.dangerous_count} 次高风险命令（最近一次：{state.last_dangerous_label}）。如果继续自主执行，必须验证影响并在最终汇报里说明风险。"
        checklist = final_review_checklist(state)
        if checklist:
            extra += "\n\n## [PLUGIN REMINDER] 最终汇报要求 / 最终汇报检查表\n" + "\n".join(f"- {item}" for item in checklist)
        if state.consecutive_low_signal >= self.settings.low_signal_block_threshold:
            extra += f"\n\n## [PLUGIN REMINDER] ⚠️ 立刻换探针\n最近连续 {state.consecutive_low_signal} 次工具调用没有带来新事实。不要重复同样的命令或同一层搜索；改用别的日志、路径、关键词、宿主、下载源或上游文档。"
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
