"""Configuration loading and normalization for Hermes plugin contexts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .constants import (
    DEFAULT_ADVISORY_INJECTION,
    DEFAULT_DANGEROUS_COMMAND_ACTION,
    DEFAULT_ENFORCEMENT_MODE,
    DEFAULT_MUTATION_BATCH_MAX,
    DEFAULT_VALIDATION_POLICY,
    LOW_SIGNAL_BLOCK_THRESHOLD,
    MAX_SUMMARY_THRESHOLD_CHARS,
    MIN_SUMMARY_THRESHOLD_CHARS,
    PLUGIN_NAME,
    SUMMARY_THRESHOLD_CHARS,
    TOOL_CATEGORIES,
)
from .models import PluginSettings, ToolCategoryName

_PLUGIN_CONFIG_KEYS = (
    PLUGIN_NAME,
    PLUGIN_NAME.replace("-", "_"),
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _coerce_int(value: Any, *, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        coerced = default
    if minimum is not None:
        coerced = max(minimum, coerced)
    if maximum is not None:
        coerced = min(maximum, coerced)
    return coerced


def _coerce_bool(value: Any, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_choice(value: Any, *, default: str, allowed: set[str]) -> str:
    choice = str(value or default).strip().lower()
    return choice if choice in allowed else default


def _coerce_action(value: Any) -> str:
    action = str(value or DEFAULT_DANGEROUS_COMMAND_ACTION).strip().lower()
    return action if action in {"approve", "block", "warn", "allow"} else DEFAULT_DANGEROUS_COMMAND_ACTION


def _coerce_validation_policy(value: Any) -> str:
    choice = str(value or DEFAULT_VALIDATION_POLICY).strip().lower()
    if choice == "immediate":
        return "after_each_mutation"
    if choice in {"batch", "after_each_mutation", "off"}:
        return choice
    return DEFAULT_VALIDATION_POLICY


def _coerce_tool_aliases(value: Any) -> dict[str, ToolCategoryName]:
    aliases: dict[str, ToolCategoryName] = {}
    for raw_name, raw_category in _as_mapping(value).items():
        name = str(raw_name or "").strip().lower()
        category = str(raw_category or "").strip().lower()
        if name and category in TOOL_CATEGORIES:
            aliases[name] = category  # type: ignore[assignment]
    return aliases


def _nested_plugin_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the plugin-specific config from common Hermes config shapes."""
    for key in _PLUGIN_CONFIG_KEYS:
        section = _as_mapping(config.get(key))
        if section:
            return section

    plugins = _as_mapping(config.get("plugins"))
    entries = _as_mapping(plugins.get("entries"))
    for key in _PLUGIN_CONFIG_KEYS:
        section = _as_mapping(entries.get(key))
        if section:
            return section

    return {}


def _read_config_from_callable(ctx: Any) -> Mapping[str, Any]:
    for method_name in ("get_plugin_config", "plugin_config", "get_config"):
        method = getattr(ctx, method_name, None)
        if not callable(method):
            continue
        for args in ((PLUGIN_NAME,), tuple()):
            try:
                value = method(*args)
            except TypeError:
                continue
            section = _as_mapping(value)
            if section:
                return _nested_plugin_config(section) or section
    return {}


def _read_config_from_hermes_runtime() -> Mapping[str, Any]:
    """Best-effort fallback for the real Hermes PluginContext.

    Hermes' runtime PluginContext does not currently expose the full config as
    an attribute, so source-tree tests with fake ctx objects can pass while the
    installed plugin silently falls back to defaults. Import lazily and swallow
    failures so the plugin remains portable outside Hermes.
    """
    try:
        from hermes_cli.config import load_config
    except ImportError:
        return {}
    try:
        return _nested_plugin_config(_as_mapping(load_config()))
    except Exception:
        return {}


def read_plugin_config(ctx: Any) -> Mapping[str, Any]:
    callable_config = _read_config_from_callable(ctx)
    if callable_config:
        return callable_config

    for attr in ("plugin_settings", "plugin_config", "settings", "config"):
        value = getattr(ctx, attr, None)
        section = _as_mapping(value)
        if not section:
            continue
        nested = _nested_plugin_config(section)
        return nested or section

    runtime_config = _read_config_from_hermes_runtime()
    if runtime_config:
        return runtime_config
    return {}


def settings_from_mapping(config: Mapping[str, Any] | None) -> PluginSettings:
    raw = _as_mapping(config)
    return PluginSettings(
        enforcement_mode=_coerce_choice(
            raw.get("enforcement_mode"),
            default=DEFAULT_ENFORCEMENT_MODE,
            allowed={"off", "advisory", "strict", "guarded"},
        ),  # type: ignore[arg-type]
        advisory_injection=_coerce_choice(
            raw.get("advisory_injection"),
            default=DEFAULT_ADVISORY_INJECTION,
            allowed={"compact", "full", "off"},
        ),  # type: ignore[arg-type]
        validation_policy=_coerce_validation_policy(raw.get("validation_policy")),  # type: ignore[arg-type]
        mutation_batch_max=_coerce_int(
            raw.get("mutation_batch_max"),
            default=DEFAULT_MUTATION_BATCH_MAX,
            minimum=1,
            maximum=20,
        ),
        dangerous_command_action=_coerce_action(raw.get("dangerous_command_action")),  # type: ignore[arg-type]
        summary_threshold_chars=_coerce_int(
            raw.get("summary_threshold_chars"),
            default=SUMMARY_THRESHOLD_CHARS,
            minimum=MIN_SUMMARY_THRESHOLD_CHARS,
            maximum=MAX_SUMMARY_THRESHOLD_CHARS,
        ),
        low_signal_block_threshold=_coerce_int(
            raw.get("low_signal_block_threshold"),
            default=LOW_SIGNAL_BLOCK_THRESHOLD,
            minimum=1,
            maximum=20,
        ),
        tool_aliases=_coerce_tool_aliases(raw.get("tool_aliases")),
        audit_enabled=_coerce_bool(raw.get("audit_enabled"), default=True),
        audit_log_path=_coerce_optional_str(raw.get("audit_log_path")),
        llm_classifier_enabled=_coerce_bool(raw.get("llm_classifier_enabled"), default=False),
        llm_classifier_provider=_coerce_optional_str(raw.get("llm_classifier_provider")),
        llm_classifier_model=_coerce_optional_str(raw.get("llm_classifier_model")),
    )


def settings_from_context(ctx: Any) -> PluginSettings:
    return settings_from_mapping(read_plugin_config(ctx))


def root_dir_from_context(ctx: Any) -> str | None:
    for attr in ("root_dir", "workspace_root", "workdir"):
        value = getattr(ctx, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
