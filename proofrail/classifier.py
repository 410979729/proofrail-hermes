"""Lightweight gray-area classifier for Proofrail.

This module intentionally stays small: deterministic workflow rules remain the
source of truth, while the classifier only handles ambiguous cases that benefit
from semantic judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .models import (
    ClassifierDecisionName,
    ClassifierEvidenceGapName,
    SessionRuntimeState,
)
from .tooling import is_likely_mutating_exec


@dataclass(slots=True)
class GuardrailClassifierDecision:
    decision: ClassifierDecisionName
    reason: str
    evidence_gap: ClassifierEvidenceGapName = "unclear"
    guidance: tuple[str, ...] = ()
    source: str = "rule"


class GuardrailClassifier(Protocol):
    def __call__(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        session_state: SessionRuntimeState,
        command: str,
        category: str,
        is_mutation: bool,
    ) -> GuardrailClassifierDecision | None: ...


@dataclass(slots=True)
class RuleBasedGrayAreaClassifier:
    """Default local classifier for ambiguous mutation scenarios.

    It only activates after at least one evidence step has happened, and only
    when the current evidence still looks broad rather than target-specific.
    """

    source: str = "rule"

    def __call__(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        session_state: SessionRuntimeState,
        command: str,
        category: str,
        is_mutation: bool,
    ) -> GuardrailClassifierDecision | None:
        if not is_mutation:
            return None
        if session_state.pending_verification or session_state.evidence_count <= 0:
            return None
        if category == "exec" and is_likely_mutating_exec(command):
            return None
        recent_evidence = (session_state.last_evidence_label or "").lower()
        broad_prefixes = ("search_files:", "web_search:", "exec observation")
        if category == "write" and session_state.phase == "execute" and recent_evidence.startswith(broad_prefixes):
            target = str(args.get("path") or args.get("file") or args.get("target") or "the target path").strip()
            return GuardrailClassifierDecision(
                decision="block",
                reason="Current evidence is still broad. Inspect the target file directly before editing.",
                evidence_gap="target_state",
                guidance=(
                    f"Inspect {target} directly before editing.",
                    "Keep the next change minimal and validate it immediately after the mutation.",
                ),
                source=self.source,
            )
        return None


@dataclass(slots=True)
class HermesLlmGuardrailClassifier:
    """Structured LLM-backed gray-area classifier.

    By default it does not pass provider/model overrides, so Hermes routes the
    call through the session's active main model. When provider/model are set,
    Hermes' plugin trust gate decides whether the override is allowed.
    """

    llm: Any
    provider: str | None = None
    model: str | None = None
    source: str = "llm"
    fallback: RuleBasedGrayAreaClassifier | None = None

    def __call__(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        session_state: SessionRuntimeState,
        command: str,
        category: str,
        is_mutation: bool,
    ) -> GuardrailClassifierDecision | None:
        payload = {
            "tool_name": tool_name,
            "category": category,
            "is_mutation": is_mutation,
            "command": command,
            "args": args,
            "session_state": {
                "phase": session_state.phase,
                "evidence_count": session_state.evidence_count,
                "pending_verification": session_state.pending_verification,
                "last_evidence_label": session_state.last_evidence_label,
                "last_mutation_label": session_state.last_mutation_label,
                "validation_suggestions": list(session_state.validation_suggestions),
                "touched_files": list(session_state.touched_files),
                "last_block_reason": session_state.last_block_reason,
            },
        }
        try:
            result = self.llm.complete_structured(
                instructions=(
                    "You are the Proofrail gray-area classifier. Follow these rules: "
                    "(1) do not override deterministic workflow blocks, "
                    "(2) prefer narrow target-state evidence before mutation, "
                    "(3) require narrow validation after mutation, "
                    "(4) if the situation is really a user choice, return ask_user, "
                    "(5) never invent file paths, commands, or evidence details."
                ),
                input=[{"type": "text", "text": str(payload)}],
                json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "decision": {"type": "string", "enum": ["allow", "warn", "ask_user", "block"]},
                        "reason": {"type": "string"},
                        "evidence_gap": {
                            "type": "string",
                            "enum": [
                                "none",
                                "target_state",
                                "change_readback",
                                "narrow_validation",
                                "user_choice",
                                "strategy_shift",
                                "unclear",
                            ],
                        },
                        "guidance": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["decision", "reason", "evidence_gap", "guidance"],
                },
                provider=self.provider,
                model=self.model,
            )
        except Exception as exc:
            if _looks_like_structured_output_unsupported(exc):
                fallback = self.fallback or RuleBasedGrayAreaClassifier(source="rule_fallback")
                return fallback(
                    tool_name=tool_name,
                    args=args,
                    session_state=session_state,
                    command=command,
                    category=category,
                    is_mutation=is_mutation,
                )
            raise
        parsed = getattr(result, "parsed", None) or {}
        normalized = normalize_classifier_decision(
            GuardrailClassifierDecision(
                decision="warn",
                reason=str(parsed.get("reason") or "").strip(),
                evidence_gap="unclear",
                guidance=tuple(str(item).strip() for item in parsed.get("guidance") or [] if str(item).strip()),
                source=self.source,
            )
        )
        if normalized is None:
            return None
        object.__setattr__(normalized, "decision", str(parsed.get("decision") or normalized.decision))
        object.__setattr__(normalized, "evidence_gap", str(parsed.get("evidence_gap") or normalized.evidence_gap))
        return normalize_classifier_decision(normalized)


def _looks_like_structured_output_unsupported(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return (
        ("json_schema" in text or "response_format" in text or "structured output" in text)
        and ("unsupported" in text or "not supported" in text or "400" in text)
    )


def should_run_classifier(
    *,
    session_state: SessionRuntimeState,
    category: str,
    is_mutation: bool,
    mutating_exec: bool,
    mutation_touches_existing_path: bool,
) -> bool:
    if not is_mutation:
        return False
    if session_state.pending_verification:
        return False
    if session_state.evidence_count <= 0:
        return False
    if category == "write" and mutation_touches_existing_path:
        return True
    if category == "exec" and mutating_exec:
        return True
    return False


def normalize_classifier_decision(value: GuardrailClassifierDecision | None) -> GuardrailClassifierDecision | None:
    if value is None:
        return None
    decision = value.decision if value.decision in {"allow", "warn", "ask_user", "block"} else "warn"
    evidence_gap = value.evidence_gap if value.evidence_gap in {
        "none",
        "target_state",
        "change_readback",
        "narrow_validation",
        "user_choice",
        "strategy_shift",
        "unclear",
    } else "unclear"
    guidance = tuple(str(item).strip() for item in value.guidance if str(item).strip())
    return GuardrailClassifierDecision(
        decision=decision,
        reason=str(value.reason or "").strip(),
        evidence_gap=evidence_gap,
        guidance=guidance,
        source=str(value.source or "rule").strip() or "rule",
    )
