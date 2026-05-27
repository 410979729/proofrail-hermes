"""Best-effort JSONL audit trail for autonomous coding sessions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from .constants import PLUGIN_NAME


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_audit_log_path(root_dir: str | None = None) -> str:
    base = Path(root_dir).expanduser() if root_dir else Path.cwd()
    return str(base / ".proofrail" / "audit.jsonl")


class AuditLogger:
    """Append-only JSONL audit trail for autonomous agent workflows.

    The audit trail is intentionally best-effort: audit failures must never break
    the user's task. The plugin is a workflow harness, not a persistence system.
    """

    def __init__(self, path: str | None = None, *, enabled: bool = True) -> None:
        self.path = Path(path).expanduser() if path else None
        self.enabled = enabled
        self._lock = RLock()

    def record(self, event: str, **fields: Any) -> None:
        if not self.enabled or self.path is None:
            return
        payload = _json_safe(
            {
                "timestamp": utc_now_iso(),
                "plugin": PLUGIN_NAME,
                "event": event,
                **fields,
            }
        )
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            # Audit is diagnostic. It should never block the runtime harness.
            return


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return repr(value)
