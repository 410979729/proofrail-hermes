"""Typed runtime models shared across the hook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

SessionPhase = Literal["observe", "execute", "review"]
EnforcementMode = Literal["off", "advisory", "guarded", "strict"]
AdvisoryInjection = Literal["compact", "full", "off"]
ValidationPolicy = Literal["batch", "after_each_mutation", "off"]
DangerousCommandAction = Literal["approve", "block", "warn", "allow"]
ToolCategoryName = Literal["read", "write", "exec", "search", "network", "other"]
ForcedNextMode = Literal["none", "gather_target_evidence", "validate_only", "change_strategy", "user_choice"]
ClassifierDecisionName = Literal["allow", "warn", "ask_user", "block"]
ClassifierEvidenceGapName = Literal[
    "none",
    "target_state",
    "change_readback",
    "narrow_validation",
    "user_choice",
    "strategy_shift",
    "unclear",
]


@dataclass(frozen=True, slots=True)
class ProofrailAdvisory:
    """A cooperative runtime advisory recorded for the next model turn."""

    reason: str
    message: str
    severity: str = "warn"
    target: str | None = None
    fastest_next_action: str | None = None
    allowed_next_actions: tuple[str, ...] = ()
    risk_if_ignored: str = "The agent may continue with stale or incomplete execution context."
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ignored: bool = False
    mode: ForcedNextMode = "none"
    source: str = "workflow"
    tool_name: str | None = None
    tool_intent: str | None = None
    command: str | None = None
    evidence_gap: str | None = None
    would_have_blocked_in_strict: bool = False


@dataclass(slots=True)
class SessionRuntimeState:
    """Mutable per-session state tracked across Hermes hook calls."""
    phase: SessionPhase = "observe"
    evidence_count: int = 0
    last_evidence_label: str | None = None
    pending_verification: bool = False
    last_mutation_label: str | None = None
    consecutive_low_signal: int = 0
    last_low_signal_signature: str | None = None
    last_low_signal_intent: str | None = None
    mutation_count: int = 0
    unverified_mutation_count: int = 0
    mutation_batch_id: str | None = None
    validation_count: int = 0
    dangerous_count: int = 0
    last_dangerous_label: str | None = None
    last_validation_label: str | None = None
    touched_files: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    validation_suggestions: tuple[str, ...] = ()
    evidence_labels: tuple[str, ...] = ()
    mutation_labels: tuple[str, ...] = ()
    validation_labels: tuple[str, ...] = ()
    dangerous_labels: tuple[str, ...] = ()
    final_report_required: bool = False
    last_block_message: str | None = None
    last_block_reason: str | None = None
    forced_next_mode: ForcedNextMode = "none"
    forced_next_target: str | None = None
    forced_next_why: str | None = None
    forced_next_exit_condition: str | None = None
    allowed_next_actions: tuple[str, ...] = ()
    forbidden_next_actions: tuple[str, ...] = ()
    last_classifier_decision: ClassifierDecisionName | None = None
    last_classifier_reason: str | None = None
    last_classifier_evidence_gap: ClassifierEvidenceGapName | None = None
    last_classifier_guidance: tuple[str, ...] = ()
    last_classifier_source: str | None = None
    pending_user_choice_signature: str | None = None
    approved_mutation_signature: str | None = None
    advisory_count: int = 0
    ignored_advisory_count: int = 0
    last_advisory: ProofrailAdvisory | None = None
    advisories: tuple[ProofrailAdvisory, ...] = ()
    enforcement_mode: EnforcementMode = "advisory"
    last_advisory_reason: str | None = None
    last_updated_at: float = 0.0


@dataclass(slots=True)
class PluginSettings:
    """Runtime settings normalized from Hermes plugin configuration."""
    enforcement_mode: EnforcementMode = "advisory"
    advisory_injection: AdvisoryInjection = "compact"
    validation_policy: ValidationPolicy = "batch"
    mutation_batch_max: int = 5
    dangerous_command_action: DangerousCommandAction = "warn"
    summary_threshold_chars: int = 8000
    low_signal_block_threshold: int = 2
    tool_aliases: dict[str, ToolCategoryName] = field(default_factory=dict)
    audit_enabled: bool = True
    audit_log_path: str | None = None
    llm_classifier_enabled: bool = False
    llm_classifier_provider: str | None = None
    llm_classifier_model: str | None = None


@dataclass(slots=True)
class ToolEvent:
    """Optional structured representation of a tool event."""
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    session_id: str = ""
    task_id: str = ""
    tool_call_id: str = ""
    duration_ms: int | None = None


@dataclass(slots=True)
class LlmContextResult:
    """Return shape for pre_llm_call context injection."""
    context: str
