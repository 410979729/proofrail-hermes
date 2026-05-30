# Configuration

The plugin works without configuration. The default behavior is optimized for autonomous Hermes instances: dangerous commands are not sent into a manual approval loop by default; they stay in autonomous warn/audit mode, but they are still blocked whenever the normal workflow guardrails require evidence first or validation first.

If your Hermes build exposes plugin config through `plugins.entries`, use this shape:

```yaml
plugins:
  enabled:
    - proofrail
  entries:
    proofrail:
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

### `dangerous_command_action`

Allowed values:

- `warn` — default. Dangerous commands are allowed, written to the audit log, and reflected back into the next `pre_llm_call` context so the agent must verify and report risk.
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
