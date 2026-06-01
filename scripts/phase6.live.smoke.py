import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from proofrail import build_runtime_hooks
from proofrail.session_state import STATE_STORE


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="proofrail.phase6.smoke."))
    target = tmp / "module.py"
    target.write_text("old\n", encoding="utf-8")
    hooks = build_runtime_hooks(root_dir=str(tmp))
    hooks.audit.path = tmp / "audit.jsonl"
    session_id = "phase6.live-smoke"
    STATE_STORE.clear(session_id)

    blocked1 = hooks.pre_tool_call(
        "write_file",
        {"path": "module.py", "content": "new\n"},
        session_id=session_id,
    )
    hooks.post_tool_call("read_file", {"path": "module.py"}, "old\n", session_id=session_id)

    allowed2 = hooks.pre_tool_call(
        "write_file",
        {"path": "module.py", "content": "new\n"},
        session_id=session_id,
    )
    hooks.post_tool_call(
        "write_file",
        {"path": "module.py", "content": "new\n"},
        {"success": True},
        session_id=session_id,
    )

    blocked3 = hooks.pre_tool_call(
        "search_files",
        {"pattern": "module", "path": str(tmp)},
        session_id=session_id,
    )
    hooks.post_tool_call("read_file", {"path": "module.py"}, "new\n", session_id=session_id)

    state = hooks.explain_state(session_id)
    context = hooks.pre_llm_call(session_id=session_id)["context"]
    entries = [
        json.loads(line)
        for line in (tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = {
        "tmp_dir": str(tmp),
        "first_block_action": None if blocked1 is None else blocked1.get("action"),
        "first_block_reason": None if blocked1 is None else blocked1.get("message", "").splitlines()[0],
        "second_write_allowed": allowed2 is None,
        "validation_block_action": None if blocked3 is None else blocked3.get("action"),
        "validation_block_reason": None if blocked3 is None else blocked3.get("message", "").splitlines()[0],
        "final_forced_mode": state["forced_next_mode"],
        "pending_verification": state["pending_verification"],
        "context_has_validation_complete": "validation complete" in context,
        "context_has_forward_progress_reopened": "forward progress reopened" in context,
        "forced_mode_events": [
            {
                "event": entry.get("event"),
                "previous_mode": entry.get("previous_mode"),
                "mode": entry.get("mode"),
                "reason": entry.get("reason"),
                "source": entry.get("source"),
                "target": entry.get("target"),
            }
            for entry in entries
            if entry.get("event") == "forced_mode_transition"
        ],
        "reopened_events": [
            {
                "event": entry.get("event"),
                "trigger": entry.get("trigger"),
                "from_mode": entry.get("from_mode"),
                "target": entry.get("target"),
            }
            for entry in entries
            if entry.get("event") == "forward_progress_reopened"
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
