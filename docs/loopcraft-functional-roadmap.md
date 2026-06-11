# LoopCraft Functional Roadmap

LoopCraft is the user-facing product direction for the current `proofrail` Hermes plugin key. The compatibility key stays `proofrail`; the product is a Hermes-native loop engineering runtime.

This roadmap is based on the current Proofrail/LoopCraft codebase plus patterns from successful agent harnesses and coding-agent plugins:

- Superpowers: composable skills and development methodology.
- OpenAI Codex: skills as authoring format, plugins as installable distribution, progressive disclosure.
- Claude Code: commands, workflows, subagents, worktrees, planning before editing, code review before shipping.
- Cline: Plan/Act mode separation, task scoping, Memory Bank, checkpoints.
- Roo Code: Boomerang task orchestration with isolated subtasks and summary handoff.
- Aider: repository map, automatic lint/test feedback after edits.
- Continue: rules, docs awareness, MCP-backed tools/context.
- OpenHands: evaluation harness, runtime/controller loop, simulated user feedback, benchmarkable agent behavior.

## Design Principles

1. **Runtime loop first, blocking second.** LoopCraft should make agents smarter through observable state and next-action guidance. Strict blocks remain compatibility/ops modes.
2. **Methodology as state, not prose.** Superpowers-style workflows should become fields in the task ledger and injected runtime panels, not only long instructions.
3. **Compatibility key remains stable.** Do not rename the Python package or Hermes plugin key away from `proofrail` until a deliberate migration layer exists.
4. **Progressive disclosure.** Keep injected context compact by default; only expand the full panel when risk, ambiguity, or explicit configuration warrants it.
5. **Every loop has an exit condition.** Each mode must say what unlocks forward progress.
6. **Generated context is not memory or user speech.** LoopCraft/Proofrail injected panels must be marked as generated runtime context; agents and memory providers should not store them in SQL, scope-recall, or long-term memory as user facts.
7. **Assistive reminders, not resistance.** Agents should evaluate LoopCraft reminders against the user request and live evidence, follow applicable reminders, and explicitly say why when a reminder is stale or wrong.
8. **Closeout includes cleanup.** After mutations, final-report guidance should include temporary artifact cleanup and retained backup/artifact disclosure.

## P0 — Highest Functional Value

### 1. Methodology Router

Add a runtime task-type analyzer that maps the current user task and tool history to a methodology profile.

Initial implementation note: LoopCraft now includes an advisory-only `task_understanding` snapshot for audit and tests, but the injected runtime panel is an **Agent Self-Routing Checkpoint**. It prompts the primary agent to make its own short phase-1 call — `intent / domain / risk / stage / next` — instead of injecting the plugin's inferred task type as an authoritative verdict. It has no control effects and is rendered as "not a permission decision".

Important correction from Joy: the plugin should not have strong control over the agent. Prior deadlocks and guardrail bugs showed that runtime interception can fail in subtle ways. The task-type analyzer may keep machine-readable audit fields, but injected guidance should help the primary agent self-route rather than act as a second LLM/classifier. Its output is for explanation, routing prompts, auditability, and next-action hygiene — never for hard control, auto policy switching, or silent permission changes.

Memory/provenance correction from Joy: plugin-injected panels are generated runtime context, not user speech. They must not be treated as user instructions, and must not be promoted into SQL/scope-recall/long-term memory as user facts. Only real user preferences, durable decisions, or verified task outcomes should be stored.

The problem is not merely "be conservative when uncertain". The implementation must model task types thoroughly enough to reduce misclassification in the first place, while still making uncertainty explicit when the evidence is incomplete.

Candidate profiles:

- `coding_change`: inspect → failing test → minimal implementation → validation → report.
- `debugging`: reproduce → isolate root cause → fix → regression test → report.
- `ops_change`: identify live target → backup → minimal mutation → health/log verification → cleanup.
- `review_only`: read diff/source → list findings by severity → no mutation.
- `research`: gather sources → cross-check → synthesize → cite limitations.

Implementation shape:

- Add `methodology_profile`, `methodology_reason`, and `methodology_next_action` to `SessionRuntimeState`.
- Audit `methodology_selected` events.
- Render a compact `## [LOOPCRAFT METHODOLOGY]` block in `pre_llm_call`.
- Keep all strong control out of this layer; it should explain and guide only.
- Add `methodology_confidence` and an explicit `uncertain` fallback. When confidence is low, render "possible workflow" rather than "current workflow".
- Prefer user intent over inference. If the user says review/只看/不要改, that must override tool-history guesses.
- Do not expand existing legacy gates. Methodology routing is advisory metadata and audit context, not enforcement.

### 2. Durable Task Ledger Snapshots

Persist compact task state so compaction/restart does not erase loop progress.

Relationship to existing audit/memory tooling:

- Proofrail already has a best-effort `.proofrail/audit.jsonl` event trail.
- `turn-closure-audit` is a separate memory-governance companion that writes redacted review candidates; it is not automatic long-term memory promotion.
- Scope Recall stores durable memories and can be queried/audited, but it should not become a raw per-tool trace sink.
- Therefore LoopCraft task ledger should fill the gap between runtime audit events and durable memory: compact, redacted, task-local state for resume/closeout, not another permanent memory dump.

Useful fields:

- task goal / current subgoal
- methodology profile
- evidence observed
- mutations made
- validation status
- pending verification target
- blocked/advisory history
- final closeout requirements

Implementation shape:

- Add optional `task_ledger_path` setting.
- Write JSONL snapshots on significant state transitions.
- Add a tiny `explain_state`/`resume_state` view that can be injected after restart or compaction.
- Add retention/cleanup policy from the beginning: max age, max size, and clear separation between task-local ledger, audit candidates, and durable memory.

### 3. Plan/Act/Verify Mode Machine

Borrow Cline's Plan/Act separation, but adapt it to Hermes runtime:

Existing baseline: old Proofrail already has `SessionPhase = observe|execute|review`, `pending_verification`, `forced_next_mode=validate_only`, advisory tracking, and final-review checklist behavior. This item should not rebuild that from scratch. It should refine the old coarse phases into clearer LoopCraft-facing labels and better closeout guidance.

- `plan`: gather evidence and propose approach; mutation discouraged.
- `act`: allow minimal mutation when evidence exists.
- `verify`: make missing validation highly visible after mutation.
- `closeout`: prompt for final evidence, cleanup status, retained artifacts, and remaining risk.

Implementation shape:

- Prefer adding a separate `loop_mode` field to preserve compatibility with existing `SessionPhase` semantics.
- Mode transitions should be automatic from tool events and optionally overridable by config.
- Avoid full-task replanning while in `verify` unless validation fails.
- Mode labels should not block tool calls. They should shape the model-readable next-action panel and final closeout checklist.

### 4. Validation Command Registry

Borrow Aider's auto-lint/auto-test concept as a suggestion/runner registry.

Important caution from Joy: bad validation config is a real risk. The first implementation should only suggest commands and explain why; it should not auto-run project-defined commands unless a future explicit safe-run policy exists.

Implementation shape:

- Configurable `validation_commands` mapping by path glob / language / repo.
- `suggest_validations()` should prefer configured commands over heuristics.
- Optional future mode: emit a `validation_candidate` event that a higher-level Hermes tool can execute.
- Validate the registry itself: parse schema, reject shell metacharacter-heavy entries by default, support `risk: low|medium|high`, and mark unknown commands as "manual review".
- Support `dry_run`/`explain` output: show matched rule, target files, command, expected success signal, and side-effect risk.

## P1 — Strong Differentiators

### 5. Repo Map / Control-Path Map

Borrow Aider's repo map idea to help agents choose the closest files before broad reading.

Important caution from Joy: dynamic call paths can be missed. The map must be treated as a navigation hint, not the truth. It should help choose what to inspect first, never declare that no other path matters.

Implementation shape:

- Generate a lightweight symbol/file map on demand or cache per repo.
- Use recent user text + touched files + imports to rank likely control-path files.
- Inject only top-ranked entries under a tight token budget.
- Record map limitations in the injected context, especially for dynamic imports, plugin discovery, reflection, shell entrypoints, generated code, and runtime config.

### 6. Checkpoints / Rollback Receipts

Borrow Cline's checkpoint concept, but keep it Git-friendly.

Joy pain point: backups are good, but repeated agent work leaves too many useless backups. Rollback receipts must be designed together with closeout cleanup, not as another pile of permanent `.bak` directories.

Implementation shape:

- Before risky mutations, record `git diff --binary` or a shadow snapshot path when inside a Git repo.
- Add rollback metadata to the task ledger.
- Do not auto-restore without explicit user approval.
- Classify backup artifacts: `temporary`, `rollback-needed`, `audit-evidence`, or `deliverable`.
- At closeout, delete temporary backups, keep only rollback/audit evidence, and report retained paths with reasons.
- Prefer compact rollback receipts over full directory copies when a Git diff or exact old-string patch is enough.

### 7. Subtask / Boomerang Handoff Protocol

Borrow Roo/Claude orchestration: parent loop delegates focused subtasks and resumes from summaries.

Implementation shape:

- Add a structured handoff format: `subtask_goal`, `context_down`, `expected_summary_up`, `verification_required`.
- Detect when a task is too broad and advise delegation instead of stuffing the main context.
- Preserve summary receipts in the ledger.

### 8. Workflow Skill Pack

Borrow Codex/Superpowers packaging:

- Keep LoopCraft runtime as the plugin.
- Add optional skills/templates as workflow authoring assets.
- Use progressive disclosure: metadata first, full instructions only when selected.

Candidate skills:

- `loopcraft-coding-change`
- `loopcraft-debugging`
- `loopcraft-ops-change`
- `loopcraft-review-only`
- `loopcraft-release-check`

## P2 — Evaluation and Ecosystem

### 9. Evaluation Harness

Borrow OpenHands evaluation ideas.

Implementation shape:

- Add synthetic tasks that simulate missing evidence, stale validation, low-signal loops, and failed tests.
- Measure whether LoopCraft moves the agent toward the right next action.
- Track advisory acceptance / ignored advisory / validation latency.

### 10. MCP / Documentation Context Integration

Borrow Continue's rules/docs/MCP model.

Implementation shape:

- Configurable `context_sources` for docs, issue trackers, CI, dashboards.
- Runtime should suggest the nearest source, not inject everything.
- Prefer source URLs/paths in the ledger for later audit.

### 11. Cost and Loop Efficiency Metrics

Borrow Cline task-management ideas.

Metrics:

- low-signal probe count
- advisory ignored count
- mutation-to-validation delay
- validation pass/fail rate
- time spent per loop mode
- tokens/context pressure if Hermes exposes it

## Immediate Next Implementation Slice

The next low-risk slice should be:

1. Add `methodology_profile` fields to `SessionRuntimeState`.
2. Implement a deterministic rule-based methodology router.
3. Render a compact methodology panel in full and compact advisory contexts.
4. Add tests for coding/debugging/ops/review routing.
5. Keep strict blocking semantics unchanged.

This slice deepens LoopCraft's functionality without risking the existing advisory/strict behavior contract.
