"""Typed runtime models shared across the hook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SessionPhase = Literal["observe", "execute", "review"]
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
    validation_count: int = 0
    dangerous_count: int = 0
    last_dangerous_label: str | None = None
    last_validation_label: str | None = None
    touched_files: tuple[str, ...] = ()
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
    last_updated_at: float = 0.0


@dataclass(slots=True)
class PluginSettings:
    """Runtime settings normalized from Hermes plugin configuration."""
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
