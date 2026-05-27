# DESIGN

## Goal

Build a **Hermes-native Python plugin** that turns a focused set of runtime workflow guardrails into a clear, testable, publishable implementation, with priority on:

1. accurate hook mapping
2. a simple, explicit state machine
3. test coverage for every rule
4. no cross-language bridge complexity

## Design Principles

### 1. Runtime > prompt

All important constraints should live in Hermes hooks:

- `pre_tool_call`
- `post_tool_call`
- `transform_tool_result`
- `pre_llm_call`
- `on_session_start`
- `on_session_end`
- `on_session_finalize`

Prompt text is only a lightweight reminder layer. It does not carry the real enforcement burden.

### 2. Shared policy, host-native wiring

Policy logic should stay reusable and testable, but host wiring must remain Hermes-native.

Shared policy includes:

- command detection
- low-signal classification
- phase transitions
- result summarization

Hermes adapter responsibilities include:

- `register(ctx)`
- Hermes hook return shapes
- path and tool-name adaptation

### 3. No cross-language bridge in v1

v1 does not use a TS runtime with a Python shim and does not spawn a Node bridge.

Why:

- Hermes plugin entrypoints are naturally Python
- state and error propagation are simplest inside Python
- open-source installation and troubleshooting stay easier for users

### 4. TDD first

Every rule should start with a failing test before implementation.

Current test coverage includes:

- existing-file mutation blocked without evidence
- mutation requires verification before the next mutation
- repeated low-signal probe blocking
- tool-result summarization
- stage-aware `pre_llm_call` context
- session end/finalize cleanup

## Runtime Model

### Session phases

The state machine intentionally keeps only three phases:

- `observe`
- `execute`
- `review`

Meaning:

- `observe`: not enough evidence yet
- `execute`: enough evidence exists for a minimal change
- `review`: a recent mutation happened and validation must run next

### State fields

Each session stores:

- `phase`
- `evidence_count`
- `last_evidence_label`
- `pending_verification`
- `last_mutation_label`
- `consecutive_low_signal`
- `last_low_signal_signature`
- `last_low_signal_intent`
- `last_updated_at`

### State lifecycle

- `on_session_start`: initialize session state
- `post_tool_call`: advance the state machine
- `on_session_end` / `on_session_finalize`: clean up session state
- prune automatically when TTL expires or capacity is exceeded

## Hook Contracts

### pre_tool_call

Returns:

```python
{"action": "block", "message": "..."}
```

Responsibilities:

- dangerous-command gating
- block existing-file mutation without evidence
- block further mutation before validation
- block repeated low-signal probing with the same intent

### post_tool_call

Responsibilities:

- classify observation / mutation / validation
- update evidence counts
- set `pending_verification`
- maintain the low-signal streak state

### transform_tool_result

Responsibilities:

- summarize oversized tool output
- reduce context pollution

### pre_llm_call

Responsibilities:

- inject phase summaries and runtime reminders
- append context rather than rewriting the system prompt

## Current v0.0.1 autonomous harness layer

v0.0.1 currently includes:

1. default `dangerous_command_action=warn`; high-risk commands stay out of a manual approval loop, but they are audited and paired with self-verification reminders
2. a JSONL audit trail for session lifecycle, tool preflight, dangerous commands, tool results, and large-output summarization
3. a validation-suggestion layer that proposes narrow follow-up checks from touched files and command shape
4. session state for mutation / validation / dangerous-command counts, touched files, validation suggestions, and recent labels
5. `pre_llm_call` injection of touched files, suggested validations, dangerous-command audit reminders, and final evidence-report requirements
6. tests covering hook registration, dangerous-command handling, evidence-before-mutation, verify-after-mutation, low-signal blocking, summarization, task ledger, audit logging, and review-state cleanup

## Version semantics

- GitHub release/tag line: `v0.0.1`
- Python package version: `0.0.1`

This split is intentional: GitHub tags keep the leading `v`, while Python packaging follows PEP 440.

## Planned Next Steps

1. add a durable task ledger for task goals, acceptance criteria, evidence, mutations, validations, and final state
2. expose `explain_state()` as a formal Hermes debug tool
3. add diff / mutation review summaries to the final review lane
4. add compaction-related snapshots and recovery anchors
5. add clean-install / wheel / plugin-dir installation smoke tests
6. validate the plugin in a live Hermes rollout
