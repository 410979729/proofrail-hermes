"""Constants and regex heuristics for the Proofrail harness.

This file centralizes policy defaults and classification patterns so runtime
modules can stay small and reviewable. The regexes are workflow heuristics, not
a security sandbox.
"""

from __future__ import annotations

import re

PLUGIN_NAME = "proofrail"
PLUGIN_VERSION = "v0.0.3"

DEFAULT_DANGEROUS_COMMAND_ACTION = "warn"
MIN_SUMMARY_THRESHOLD_CHARS = 1000
MAX_SUMMARY_THRESHOLD_CHARS = 50000
SUMMARY_THRESHOLD_CHARS = 8000
SUMMARY_KEEP_HEAD = 2000
SUMMARY_KEEP_TAIL = 1500
SESSION_STATE_TTL_SECONDS = 6 * 60 * 60
MAX_SESSION_STATES = 128
LOW_SIGNAL_BLOCK_THRESHOLD = 2
MAX_EVIDENCE_COUNT = 8

TOOL_CATEGORIES = {"read", "write", "exec", "search", "network", "other"}
DEFAULT_TOOL_ALIASES: dict[str, str] = {
    "read": "read",
    "read_file": "read",
    "browser_snapshot": "read",
    "browser_console": "read",
    "vision_analyze": "read",
    "write_file": "write",
    "patch": "write",
    "search_files": "search",
    "web_search": "search",
    "browser_navigate": "network",
    "browser_click": "network",
    "terminal": "exec",
    "execute_code": "exec",
}

NEW_BEHAVIOR_RULES = """
## [SYSTEM STATUS — not user input]
- Treat this as runtime state, not as a second user or reviewer.
- Before mutating existing files or processes, inspect the closest local artifact on the real control path.
- After each mutation, run the narrowest relevant validation before making more changes.
- If repeated probes add no new facts, switch path, log source, keyword set, host, or validation method.
- In final reports after changes, include root cause, changes, validation, evidence, and remaining risks.
""".strip()

DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?=.*\brm\b)(?=.*--no-preserve-root)", re.I), "rm --no-preserve-root"),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+(?:/|~)(?:\s|$)", re.I), "rm -rf root/home"),
    (re.compile(r"\brm\s+-[a-z]*f[a-z]*r[a-z]*\s+(?:/|~)(?:\s|$)", re.I), "rm -fr root/home"),
    (re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+push\b.*\s(?:-f|--force|--force-with-lease)(?:\s|$)", re.I), "git push --force"),
    (re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+reset\s+--hard", re.I), "git reset --hard"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA|USER)\b", re.I), "DROP TABLE/DATABASE"),
    (re.compile(r"\bTRUNCATE\s+(TABLE\s+)?\w+", re.I), "TRUNCATE TABLE"),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b"), "chmod 777"),
    (re.compile(r"\bmkfs\b"), "mkfs"),
    (re.compile(r"\bdd\s+.*of=/dev/"), "dd of=/dev/"),
    (re.compile(r"\bkill\s+-9\s+(-1|1)\b"), "kill -9 PID 1"),
    (re.compile(r"\b(pkill|killall)\s+-9\s+(init|systemd)\b"), "killall init/systemd"),
    (re.compile(r"\btailscale\s+(down|uninstall|logout)\b"), "tailscale down/uninstall"),
    (re.compile(r"\bsystemctl\s+(stop|disable)\s+tailscaled\b"), "stop tailscaled"),
    (re.compile(r"\b(?:curl|wget)\b.*(?:\||>)\s*(?:sh|bash)\b", re.I), "curl/wget pipe to shell"),
    (re.compile(r"\bsudo\s+rm\b.*\s-rf\b", re.I), "sudo rm -rf"),
]

MUTATING_EXEC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(npm|pnpm|yarn|bun|pip|pip3|uv|poetry)\s+(install|add|remove|uninstall|update)\b", re.I),
    re.compile(r"\b(systemctl|service)\s+(start|stop|restart|reload|enable|disable)\b", re.I),
    re.compile(r"\b(docker|podman)\s+(run|start|stop|restart)\b", re.I),
    re.compile(r"\bdocker\s+compose\s+(up|down|restart)\b", re.I),
    re.compile(r"\b(kill|pkill|killall)\b", re.I),
    re.compile(r"\b(git\s+(apply|am|cherry-pick|merge|rebase|reset|checkout|restore|switch|add|commit|push|stash))\b", re.I),
    re.compile(r"\b(mv|cp|rm|mkdir|rmdir|ln|chmod|chown|touch)\b"),
    re.compile(r"\b(sed|perl)\b.*\s-i\b", re.I),
    re.compile(r"\b(prettier|eslint|ruff)\b.*\s(--write|--fix)\b", re.I),
    re.compile(r"\b(?:python(?:3)?\s+)?manage\.py\s+migrate\b", re.I),
    re.compile(r"\b(?:rails|rake)\s+db:migrate\b", re.I),
    re.compile(r"\b(DROP\s+(TABLE|DATABASE|SCHEMA|USER)|TRUNCATE\s+(TABLE\s+)?\w+)\b", re.I),
    re.compile(r"\b(?:curl|wget)\b.*\|\s*(?:sh|bash)\b", re.I),
    re.compile(r"\bpython(?:3)?\b.*\bopen\([^\n]*,['\"](?:w|a|x)[b+]?['\"]\)", re.I),
    # Shell redirection. Require start/whitespace before the operator so comparisons
    # such as `2>=1` or strings like `a>b` are not treated as mutations.
    re.compile(r"(?:^|\s)\d?>>\s*[^\s&|;]+"),
    re.compile(r"(?:^|\s)\d?>\s*(?![=>])[^\s&|;]+"),
]

VALIDATION_EXEC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(pytest|jest|vitest|mocha|rspec|phpunit|cargo test|go test)\b", re.I),
    re.compile(r"\b(npm|pnpm|yarn|bun)\s+(test|run\s+(test|lint|build|typecheck))\b", re.I),
    re.compile(r"\b(tsc|eslint|ruff|mypy|cargo check|go test)\b", re.I),
    re.compile(r"\b(systemctl\s+status)\b", re.I),
    re.compile(r"\b(ss|netstat|lsof|curl)\b", re.I),
]

LOW_SIGNAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^$"),
    re.compile(r"^(ok|done|ready|success|completed)$", re.I),
    re.compile(r"^no (matches|results?|output)\b", re.I),
    re.compile(r"^0 (matches|results?)\b", re.I),
    re.compile(r"^not found$", re.I),
]

PLAIN_TEXT_FAILURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\btraceback \(most recent call last\)", re.I),
    re.compile(r"\b(permission denied|command not found|no such file or directory)\b", re.I),
    re.compile(r"(^|\n)\s*(error|exception|failed|failure)\s*[:\-]", re.I),
    re.compile(r"\b(failed|failure|exception)\b", re.I),
]
