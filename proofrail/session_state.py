"""Session-scoped workflow state machine for the autonomous harness."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from threading import RLock
from typing import Any

from .constants import LOW_SIGNAL_PATTERNS, MAX_EVIDENCE_COUNT, MAX_SESSION_STATES, SESSION_STATE_TTL_SECONDS
from .models import (
    ClassifierDecisionName,
    ClassifierEvidenceGapName,
    SessionRuntimeState,
)
from .path_utils import get_path_hints
from .validation import summarize_paths
from .text_utils import compact_label, first_string_field, normalize_signal_text
from .tooling import ToolCategory, get_exec_command, get_tool_category, normalize_tool_name


class SessionStateStore:
    """Thread-safe in-memory session store with TTL pruning."""

    def __init__(self) -> None:
        self._states: dict[str, SessionRuntimeState] = {}
        self._lock = RLock()

    def _prune_unlocked(self) -> None:
        now = time.time()
        stale = [key for key, state in self._states.items() if now - state.last_updated_at > SESSION_STATE_TTL_SECONDS]
        for key in stale:
            self._states.pop(key, None)
        if len(self._states) <= MAX_SESSION_STATES:
            return
        oldest = sorted(self._states.items(), key=lambda item: item[1].last_updated_at)
        for key, _ in oldest[: len(self._states) - MAX_SESSION_STATES]:
            self._states.pop(key, None)

    def _get_unlocked(self, session_id: str) -> SessionRuntimeState:
        key = session_id or "default"
        self._prune_unlocked()
        state = self._states.get(key)
        if state is None:
            state = SessionRuntimeState(last_updated_at=time.time())
            self._states[key] = state
        else:
            state.last_updated_at = time.time()
        return state

    def get(self, session_id: str) -> SessionRuntimeState:
        with self._lock:
            return self._get_unlocked(session_id)

    def snapshot(self, session_id: str) -> SessionRuntimeState:
        with self._lock:
            return replace(self._get_unlocked(session_id))

    def peek(self, session_id: str) -> SessionRuntimeState | None:
        with self._lock:
            self._prune_unlocked()
            state = self._states.get(session_id or "default")
            return replace(state) if state is not None else None

    def update(self, session_id: str, updater: Callable[[SessionRuntimeState], None]) -> SessionRuntimeState:
        with self._lock:
            state = self._get_unlocked(session_id)
            updater(state)
            state.last_updated_at = time.time()
            return replace(state)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._states.pop(session_id or "default", None)


STATE_STORE = SessionStateStore()


def describe_observation(tool_name: str, input_data: dict[str, Any], tool_aliases: Mapping[str, str] | None = None) -> str:
    normalized = normalize_tool_name(tool_name)
    if get_tool_category(tool_name, tool_aliases) == "exec":
        command = get_exec_command(input_data)
        return f"exec observation: {compact_label(command, 100)}" if command else "exec observation"
    target = (
        get_path_hints(input_data)[0] if get_path_hints(input_data) else ""
    ) or first_string_field(input_data, ["query", "pattern", "url", "uri"]) or normalized or tool_name
    return f"{normalized or tool_name}: {compact_label(target, 100)}"


def build_tool_intent_signature(
    tool_name: str,
    input_data: dict[str, Any],
    tool_aliases: Mapping[str, str] | None = None,
) -> str:
    normalized = normalize_tool_name(tool_name)
    if get_tool_category(tool_name, tool_aliases) == "exec":
        command = get_exec_command(input_data)
        return f"exec:{compact_label(command, 180).lower()}" if command else "exec"
    hints = get_path_hints(input_data)
    focus = hints[0] if hints else ""
    focus = focus or first_string_field(input_data, ["query", "pattern", "url", "uri"]) or normalized or tool_name
    return f"{normalized or tool_name}:{compact_label(focus, 180).lower()}"


def is_evidence_observation(category: ToolCategory, mutating_exec: bool, low_signal: bool, error_text: str) -> bool:
    if low_signal or error_text:
        return False
    if category in {"read", "search", "network"}:
        return True
    if category == "exec" and not mutating_exec:
        return True
    return False


def is_low_signal_observation(tool_name: str, text: str, error_text: str) -> bool:
    if error_text:
        return False
    normalized_tool = normalize_tool_name(tool_name)
    normalized = normalize_signal_text(text)
    if not normalized:
        return True
    if len(normalized) >= 120:
        return False
    if any(pattern.search(normalized) for pattern in LOW_SIGNAL_PATTERNS):
        return True
    if normalized_tool in {"search_files", "web_search"} and any(word in normalized for word in ["no matches", "no results", "0 matches", "0 results", "not found"]):
        return True
    if normalized_tool == "terminal" and normalized in {"ok", "done", "ready", "success", "completed"}:
        return True
    return False


def describe_mutation(tool_name: str, input_data: dict[str, Any], tool_aliases: Mapping[str, str] | None = None) -> str:
    normalized = normalize_tool_name(tool_name)
    category = get_tool_category(tool_name, tool_aliases)
    if category == "exec":
        command = get_exec_command(input_data)
        return f"exec: {command[:100]}"
    target = first_string_field(input_data, ["path", "filePath", "file", "target"]) or normalized or tool_name
    if category == "write":
        return f"write: {target[:100]}"
    return f"{normalized or tool_name}: {target[:100]}"


def record_tool_observation(
    *,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
    text: str,
    error_text: str,
    mutating_exec: bool,
    validation_succeeded: bool,
    tool_aliases: Mapping[str, str] | None = None,
    touched_paths: list[str] | tuple[str, ...] | None = None,
    validation_suggestions: list[str] | tuple[str, ...] | None = None,
) -> SessionRuntimeState:
    def apply(state: SessionRuntimeState) -> None:
        category = get_tool_category(tool_name, tool_aliases)
        tool_intent = build_tool_intent_signature(tool_name, args, tool_aliases)
        low_signal = False if validation_succeeded else is_low_signal_observation(tool_name, text, error_text)
        low_signal_signature = normalize_signal_text(text)[:160] or f"{tool_name}:empty"

        if low_signal:
            if state.last_low_signal_signature == low_signal_signature:
                state.consecutive_low_signal += 1
            else:
                state.consecutive_low_signal = max(1, state.consecutive_low_signal + 1)
            state.last_low_signal_signature = low_signal_signature
            state.last_low_signal_intent = tool_intent
        else:
            state.consecutive_low_signal = 0
            state.last_low_signal_signature = None
            state.last_low_signal_intent = None

        if is_evidence_observation(category, mutating_exec, low_signal, error_text):
            state.evidence_count = min(state.evidence_count + 1, MAX_EVIDENCE_COUNT)
            state.last_evidence_label = describe_observation(tool_name, args, tool_aliases)
            state.evidence_labels = _merge_tuple(state.evidence_labels, [state.last_evidence_label])
            if not state.pending_verification:
                state.phase = "execute"
            if state.last_block_reason in {"missing_evidence", "low_signal_repeat"}:
                state.last_block_message = None
                state.last_block_reason = None

        if category == "write" or mutating_exec:
            state.pending_verification = True
            state.last_mutation_label = describe_mutation(tool_name, args, tool_aliases)
            state.mutation_count += 1
            state.final_report_required = True
            state.mutation_labels = _merge_tuple(state.mutation_labels, [state.last_mutation_label])
            state.phase = "review"
            state.touched_files = _merge_tuple(state.touched_files, summarize_paths(touched_paths or []))
            state.validation_suggestions = _merge_tuple(state.validation_suggestions, validation_suggestions or [])
        elif state.pending_verification and validation_succeeded:
            state.pending_verification = False
            state.last_mutation_label = None
            state.validation_count += 1
            state.last_validation_label = describe_observation(tool_name, args, tool_aliases)
            state.validation_labels = _merge_tuple(state.validation_labels, [state.last_validation_label])
            state.validation_suggestions = ()
            state.phase = "execute" if state.evidence_count > 0 else "observe"
            if state.last_block_reason == "pending_verification":
                state.last_block_message = None
                state.last_block_reason = None

    return STATE_STORE.update(session_id, apply)


def record_block_decision(session_id: str, message: str, reason: str) -> SessionRuntimeState:
    def apply(state: SessionRuntimeState) -> None:
        state.last_block_message = message
        state.last_block_reason = reason

    return STATE_STORE.update(session_id, apply)


def record_classifier_decision(
    session_id: str,
    *,
    decision: ClassifierDecisionName,
    reason: str,
    evidence_gap: ClassifierEvidenceGapName,
    guidance: list[str] | tuple[str, ...],
    source: str,
) -> SessionRuntimeState:
    def apply(state: SessionRuntimeState) -> None:
        state.last_classifier_decision = decision
        state.last_classifier_reason = reason.strip() or None
        state.last_classifier_evidence_gap = evidence_gap
        state.last_classifier_guidance = _merge_tuple((), guidance, limit=6)
        state.last_classifier_source = source.strip() or None

    return STATE_STORE.update(session_id, apply)


def clear_classifier_decision(session_id: str) -> SessionRuntimeState:
    def apply(state: SessionRuntimeState) -> None:
        state.last_classifier_decision = None
        state.last_classifier_reason = None
        state.last_classifier_evidence_gap = None
        state.last_classifier_guidance = ()
        state.last_classifier_source = None

    return STATE_STORE.update(session_id, apply)


def record_dangerous_command(session_id: str, label: str) -> SessionRuntimeState:
    def apply(state: SessionRuntimeState) -> None:
        state.dangerous_count += 1
        state.last_dangerous_label = label
        state.dangerous_labels = _merge_tuple(state.dangerous_labels, [label])

    return STATE_STORE.update(session_id, apply)


def _merge_tuple(existing: tuple[str, ...], incoming: list[str] | tuple[str, ...], *, limit: int = 12) -> tuple[str, ...]:
    seen = set(existing)
    out = list(existing)
    for value in incoming:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return tuple(out[-limit:])
