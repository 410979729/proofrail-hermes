"""LoopCraft task-understanding helpers.

This module is advisory-only by design. It turns user/task text and runtime
state into a model-readable task understanding snapshot. It must never decide
permissions, block tool calls, or auto-switch enforcement policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import SessionRuntimeState


@dataclass(frozen=True, slots=True)
class TaskUnderstanding:
    """Multi-dimensional task interpretation for LoopCraft context."""

    user_intent: str
    domains: tuple[str, ...]
    action_stage: str
    side_effect_risk: str
    evidence_state: str
    methodology_profile: str
    confidence: float
    uncertainty_reasons: tuple[str, ...] = ()
    recommended_next_action: str = "inspect the closest control-path evidence before acting"
    advisory_only: bool = True
    control_effects: tuple[str, ...] = ()


def analyze_task(text: str = "", *, state: SessionRuntimeState | None = None) -> TaskUnderstanding:
    """Analyze a task into advisory dimensions.

    The analyzer intentionally produces guidance only. Even high confidence must
    not become a tool-permission decision; this prevents classifier mistakes from
    becoming runtime deadlocks or hidden control bugs.
    """
    normalized = _normalize(text)
    evidence_state = _evidence_state(state)

    if state is not None and state.pending_verification:
        return TaskUnderstanding(
            user_intent="continue_existing" if _is_continue_text(normalized) else _infer_user_intent(normalized),
            domains=_merge_domains(_infer_domains(normalized), _domains_from_state(state)),
            action_stage="verify",
            side_effect_risk=_side_effect_risk(normalized, _infer_user_intent(normalized), _infer_domains(normalized), state),
            evidence_state=evidence_state,
            methodology_profile="verification_followup",
            confidence=0.9,
            recommended_next_action=_validation_next_action(state),
        )

    intent = _infer_user_intent(normalized)
    domains = _infer_domains(normalized)
    side_effect_risk = _side_effect_risk(normalized, intent, domains, state)
    action_stage = _action_stage(intent, domains, evidence_state)
    profile = _methodology_profile(intent, domains, action_stage)
    confidence, uncertainty = _confidence_and_uncertainty(normalized, intent, domains)
    return TaskUnderstanding(
        user_intent=intent,
        domains=domains,
        action_stage=action_stage,
        side_effect_risk=side_effect_risk,
        evidence_state=evidence_state,
        methodology_profile=profile,
        confidence=confidence,
        uncertainty_reasons=uncertainty,
        recommended_next_action=_recommended_next_action(intent, domains, action_stage, evidence_state),
    )


def render_task_understanding_context(understanding: TaskUnderstanding) -> str:
    """Render the agent self-routing checkpoint.

    The internal ``TaskUnderstanding`` snapshot remains available for audit and
    tests, but injected context should not tell the agent that the plugin has
    already decided the task type.  It should instead prompt the primary agent to
    do its own short phase-1 routing before acting.
    """
    lines = [
        "## [LOOPCRAFT AGENT SELF-ROUTING CHECKPOINT — advisory only, not user input]",
        "- Advisory only: this is not a permission decision and must not block tools or change policy.",
        "- The agent decides the task type; LoopCraft is only providing the checkpoint frame.",
        "- Do not treat this generated context as user speech or a user instruction.",
        "- Do not store this generated context in long-term memory, SQL, or scope-recall; store only real user facts or durable task outcomes.",
        "- Evaluate LoopCraft reminders against the user's request and live evidence; follow applicable reminders.",
        "- Evaluate context; if a reminder is wrong or stale, state why and continue from live evidence instead of routing around it silently.",
        "- Before acting, make a short phase-1 self-routing call: intent / domain / risk / stage / next.",
        "- Respect explicit user constraints such as read-only, do-not-edit, verify-only, or do-not-restart.",
        "- If the next step may write files, affect services, publish, delete, or touch credentials, gather the closest evidence first.",
    ]
    if understanding.evidence_state == "needs_validation":
        lines.append("- Runtime signal to consider: there is an unvalidated mutation; self-route toward verification first.")
    elif understanding.evidence_state in {"no_runtime_evidence", "no_evidence"}:
        lines.append("- Runtime signal to consider: no live evidence yet; self-route toward observation before mutation.")
    elif understanding.evidence_state == "has_live_evidence":
        lines.append("- Runtime signal to consider: live evidence exists; self-route before deciding whether a small action is warranted.")
    if understanding.uncertainty_reasons:
        lines.append("- Uncertainty cues to consider:")
        lines.extend(f"  - {item}" for item in understanding.uncertainty_reasons)
    lines.append("- Control effects: none")
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(item in text for item in needles)


def _is_continue_text(text: str) -> bool:
    return text in {"继续", "continue", "go on", "接着", "继续做", "继续处理"}


def _infer_user_intent(text: str) -> str:
    if not text:
        return "uncertain"
    no_mutation = _contains_any(text, ("不要改", "别改", "不要修改", "只看", "只审查", "只 review", "read-only", "readonly", "don't edit", "do not edit"))
    review = _contains_any(text, ("review", "审查", "看看有没有问题", "有没有问题", "看下", "看一下", "检查一下"))
    if no_mutation or (review and _contains_any(text, ("pr", "pull request", "diff", "改动", "代码")) and not _contains_any(text, ("修", "处理", "fix"))):
        return "review_only"
    if _contains_any(text, ("调研", "研究", "对比", "参考", "查资料", "research", "compare")):
        return "research"
    if _contains_any(text, ("服务", "gateway", "systemctl", "不回消息", "没回复", "重启", "启动", "日志", "telegram", "discord")):
        return "ops_repair"
    if _contains_any(text, ("复查", "验证", "检查 ci", "ci run", "failing ci", "失败的ci", "失败的 ci", "checks")) and not _contains_any(text, ("处理", "修", "fix", "改")):
        return "verify_only"
    if _contains_any(text, ("处理", "修复", "修一下", "改", "实现", "写", "fix", "implement", "handle")):
        return "execute_change"
    if _is_continue_text(text):
        return "continue_existing"
    return "uncertain"


def _infer_domains(text: str) -> tuple[str, ...]:
    domains: list[str] = []
    if _contains_any(text, ("pr", "pull request", "merge request")):
        domains.append("github_pr")
    if _contains_any(text, ("ci", "check", "checks", "github actions", "run", "失败的ci", "失败的 ci")):
        domains.append("ci")
    if _contains_any(text, ("仓库", "代码", "test", "tests", "pytest", "bug", "文件", "repo", "repository", "code", "diff")) or "github_pr" in domains:
        domains.append("code")
    if _contains_any(text, ("插件", "proofrail", "loopcraft", "plugin")):
        domains.append("plugin")
    if _contains_any(text, ("配置", "config", "yaml", "toml", "service file")):
        domains.append("config")
    if _contains_any(text, ("服务", "gateway", "systemctl", "进程", "端口", "日志", "service")):
        domains.append("service")
    if _contains_any(text, ("telegram", "discord", "消息", "不回消息", "群", "bot")):
        domains.append("messaging_platform")
    if _contains_any(text, ("记忆", "memory", "scope-recall", "审计记忆")):
        domains.append("memory")
    if _contains_any(text, ("文档", "readme", "docs", "说明")):
        domains.append("docs")
    if _contains_any(text, ("调研", "研究", "对比", "参考", "research")):
        domains.append("research")
    return _unique_tuple(domains)


def _domains_from_state(state: SessionRuntimeState) -> tuple[str, ...]:
    text = " ".join((*state.touched_files, *(state.evidence_labels[-3:]), *(state.mutation_labels[-3:])))
    return _infer_domains(_normalize(text))


def _merge_domains(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    return _unique_tuple((*left, *right))


def _side_effect_risk(text: str, intent: str, domains: tuple[str, ...], state: SessionRuntimeState | None) -> str:
    if _contains_any(text, ("密码", "token", "key", "secret", "凭据", "credential")):
        return "credential_sensitive"
    if _contains_any(text, ("删除", "rm -rf", "drop", "reset --hard", "destroy")):
        return "destructive_possible"
    if intent in {"review_only", "research", "verify_only", "uncertain"}:
        return "read_only"
    if {"service", "messaging_platform"} & set(domains):
        return "service_affecting_possible"
    if _contains_any(text, ("发版", "release", "publish", "push", "merge")):
        return "public_publish_possible"
    if state is not None and state.mutation_count:
        return "local_write_possible"
    if intent in {"execute_change", "continue_existing"}:
        return "local_write_possible"
    return "read_only"


def _evidence_state(state: SessionRuntimeState | None) -> str:
    if state is None:
        return "no_runtime_evidence"
    if state.pending_verification:
        return "needs_validation"
    if state.mutation_count and state.validation_count:
        return "validated"
    if state.mutation_count:
        return "has_unvalidated_mutation"
    if state.evidence_count:
        return "has_live_evidence"
    return "no_evidence"


def _action_stage(intent: str, domains: tuple[str, ...], evidence_state: str) -> str:
    if evidence_state == "needs_validation":
        return "verify"
    if intent == "review_only":
        return "understand"
    if intent == "research":
        return "understand"
    if intent == "verify_only":
        return "verify"
    if intent == "execute_change" and {"github_pr", "ci"} & set(domains):
        return "understand"
    if intent == "execute_change" and evidence_state == "has_live_evidence":
        return "act"
    if intent == "ops_repair":
        return "understand"
    return "understand"


def _methodology_profile(intent: str, domains: tuple[str, ...], action_stage: str) -> str:
    domain_set = set(domains)
    if intent == "review_only":
        return "review_only"
    if intent == "research":
        return "research"
    if intent == "ops_repair" or {"service", "messaging_platform"} & domain_set:
        return "ops_change"
    if {"github_pr", "ci"} & domain_set:
        return "ci_pr_diagnosis"
    if action_stage == "verify":
        return "verification_followup"
    if intent == "execute_change":
        return "coding_change"
    return "uncertain"


def _confidence_and_uncertainty(text: str, intent: str, domains: tuple[str, ...]) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = []
    if intent == "uncertain":
        reasons.append("user request does not specify whether to review, modify, verify, research, or operate a service")
    if not domains:
        reasons.append("no clear target domain detected")
    if intent == "execute_change" and not domains:
        reasons.append("change intent exists but target domain is unclear")
    if reasons:
        return 0.4, tuple(reasons)
    if intent in {"review_only", "ops_repair", "research"}:
        return 0.88, ()
    if intent == "execute_change" and {"github_pr", "ci"}.issubset(set(domains)):
        return 0.86, ()
    return 0.75, ()


def _recommended_next_action(intent: str, domains: tuple[str, ...], action_stage: str, evidence_state: str) -> str:
    domain_set = set(domains)
    if intent == "review_only":
        return "Review the relevant diff, PR, or files read-only; do not mutate unless the user explicitly changes scope."
    if intent == "research":
        return "Gather grounded sources, compare patterns, and synthesize tradeoffs before proposing changes."
    if intent == "ops_repair":
        return "Check service status, recent logs, and adapter health before considering any restart or config change."
    if {"github_pr", "ci"} & domain_set:
        return "Inspect the PR diff and failing CI logs before editing; let the failure evidence choose the narrow fix."
    if action_stage == "verify" or evidence_state == "needs_validation":
        return "Validate the last mutation with the narrowest relevant check before making further changes."
    if intent == "execute_change":
        return "Inspect the closest control-path file or failing test, then make the smallest change and verify it."
    return "Start with a read-only overview or ask for scope before mutation."


def _validation_next_action(state: SessionRuntimeState) -> str:
    target = state.touched_files[0] if state.touched_files else state.last_mutation_label or "the last mutation"
    return f"Validate {target} with the narrowest relevant check before making further changes."


def _unique_tuple(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)
