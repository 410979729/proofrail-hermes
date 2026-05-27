# Security Policy

## Scope

This plugin enforces runtime guardrails inside Hermes via hook callbacks. It is a defensive workflow plugin, not a sandbox.

## Reporting

Please report security issues privately to the maintainer before opening a public issue.

## Current Security Boundaries

- Dangerous command detection is pattern-based, not a full shell parser.
- Mutation detection for `terminal` is heuristic and best-effort.
- The plugin reduces risky behavior but does not replace OS permissions, approval flows, or environment isolation.
- When audit is enabled, logs may contain command text, paths, tool arguments, and output previews; operators should treat audit logs as potentially sensitive.

## Safe Disclosure Expectations

Please include:

- the exact command or tool call shape
- expected vs actual guardrail behavior
- whether the issue requires prior evidence state
- a minimal reproduction if possible
