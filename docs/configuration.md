# Configuration

The plugin works without configuration. The default behavior is optimized for autonomous Hermes instances: `enforcement_mode` defaults to `advisory`, so workflow risks are recorded as compact next-action advisories instead of blocking the tool call. Dangerous commands default to `warn`, which records advisory + audit and allows the command unless you choose a stricter command policy.

If your Hermes build exposes plugin config through `plugins.entries`, use this shape:

```yaml
plugins:
  enabled:
    - proofrail
  entries:
    proofrail:
      enforcement_mode: advisory
      advisory_injection: compact
      validation_policy: batch
      mutation_batch_max: 5
      dangerous_command_action: warn
      summary_threshold_chars: 8000
      low_signal_block_threshold: 2
      audit_enabled: true
      audit_log_path: .proofrail/audit.jsonl
      llm_classifier_enabled: true
      # Leave provider/model unset to inherit the current session's main model.
      llm_classifier_provider: null
      llm_classifier_model: null
      tool_aliases:
        shell: exec
        run_command: exec
        edit_file: write
        apply_patch: write
        file_read: read
```

## Options

### `enforcement_mode`

Allowed values:

- `advisory` — default. Workflow risks are recorded, audited, and injected as compact next-action guidance; the tool call continues.
- `strict` — preserve the older hard-block/cooperative-mode behavior for missing evidence, pending verification, broad evidence, and repeated low-signal probes.
- `guarded` — workflow risks behave like advisory, but critical dangerous commands hard-block unless `dangerous_command_action: allow` is set. `block` and `approve` remain fail-closed.
- `off` — disable workflow-risk advisories/blocks while leaving other plugin plumbing available.

### `advisory_injection`

Allowed values:

- `compact` — default. Inject a short advisory action card when there is an open advisory.
- `full` — render the broader runtime context/task panel style when there is an open advisory or other risk state.
- `off` — do not inject advisory cards. The compact base `SYSTEM STATUS` context can still be injected; state and audit still update.

### `validation_policy`

Allowed values:

- `batch` — default. Track unverified mutations and escalate advisory severity as the batch grows. In strict mode, mutation continues until `mutation_batch_max` is reached, then validation is required before more mutation.
- `after_each_mutation` — compatibility spelling for immediate validation expectations; old `immediate` config values are mapped here. In strict mode, the next mutation is blocked until validation completes.
- `off` — do not create pending-verification workflow advisories/blocks.

### `mutation_batch_max`

Maximum unverified mutation batch size before advisory severity escalates. Defaults to `5` and is clamped between `1` and `20`.

### `dangerous_command_action`

Allowed values:

- `warn` — default. Dangerous commands are allowed, written to the audit log, and reflected back into the next `pre_llm_call` context so the agent must verify and report risk. In `guarded` mode, critical dangerous commands hard-block under `warn`.
- `allow` — dangerous commands are allowed and audited, without warning text being treated as a policy event.
- `block` — dangerous commands return `{"action": "block"}`.
- `approve` — **not auto-allow**. This is a fail-closed/manual-confirmation label for hosts that want explicit human gating semantics. In the current Hermes implementation it still returns a blocking decision and tells the operator to confirm manually, then retry the command.

Invalid values fall back to `warn`.

### `summary_threshold_chars`

Large tool outputs above this size are summarized before they return to the model context.

The value is clamped between 1000 and 50000.

### `low_signal_block_threshold`

How many repeated low-signal observations with the same intent are allowed before blocking repeated probes.

The value is clamped between 1 and 20.

### `audit_enabled`

Controls whether JSONL audit events are written. Defaults to `true`.

Audit failures are best-effort and never block the user's task.

By default, audit events may include command text, tool arguments, touched paths, and short output previews. Do not pass reusable secrets directly in command-line flags or tool arguments if audit is enabled.

### `audit_log_path`

Optional path for the JSONL audit trail. If omitted, the runtime uses:

```text
<root_dir>/.proofrail/audit.jsonl
```

If Hermes does not provide a root directory, the current working directory is used.

### `llm_classifier_enabled`

Enables the gray-area classifier path.

- `false` — default. Proofrail only uses deterministic workflow rules.
- `true` — when the host exposes `ctx.llm`, Proofrail adds an LLM-backed gray-area classifier after deterministic checks pass.

The classifier never overrides deterministic blocks such as missing evidence or pending verification.

### `llm_classifier_provider`

Optional provider override for the classifier model.

If omitted or set to `null`, Proofrail does **not** force a provider and Hermes routes the classifier call through the current session's active main provider.

### `llm_classifier_model`

Optional model override for the classifier model.

If omitted or set to `null`, Proofrail does **not** force a model and Hermes routes the classifier call through the current session's active main model.

Set `llm_classifier_provider` and `llm_classifier_model` together when you want the classifier to use a dedicated model. Leave both unset when you want it to follow the main model automatically.

### `tool_aliases`

Maps host-specific tool names to one of:

- `read`
- `write`
- `exec`
- `search`
- `network`
- `other`

Unknown categories are ignored.

## Notes on built-in dangerous-command patterns

Some default dangerous-command patterns are intentionally opinionated and include common fleet/network protection cases such as Tailscale shutdown or logout commands. They are best-effort defaults, not a claim that every deployment uses Tailscale. If your environment has different safety boundaries, adjust the plugin source or maintain a downstream fork/profile-specific build.
