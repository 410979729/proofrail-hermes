"""Typed runtime models shared across the hook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SessionPhase = Literal["observe", "execute", "review"]
DangerousCommandAction = Literal["approve", "block", "warn", "allow"]
ToolCategoryName = Literal["read", "write", "exec", "search", "network", "other"]


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
