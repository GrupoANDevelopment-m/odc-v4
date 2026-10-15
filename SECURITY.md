# Security Policy

## Reporting vulnerabilities

Email: security@odc-v4.local (or open a private issue)

We respond within 48 hours.

## Hard rules (system invariants)

ODC v4 enforces 10 invariants that cannot be bypassed:

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| I1 | No `os.system` / shell=True | AST check on dynamic tool bodies |
| I2 | Network only to allow-listed domains | `WorkspacePolicy` + `WebAllowList` |
| I3 | File writes only inside workspace roots | `WorkspacePolicy.allows()` |
| I4 | `require_safety` UNBREAKABLE | `auto_extend.set_gate()` rejects `False` |
| I5 | Audit trail append-only (HMAC-chained) | `AuditTrail.verify_chain()` on every read |
| I6 | Cognitive profile thread-safe | `threading.RLock` + atomic write |
| I7 | Constitutional guard runs on every patch | `_apply_change` always calls `check_safety` |
| I8 | Rate limits enforced | `MAX_HEAL_PER_HOUR = 10` |
| I9 | Sandbox test required | `load_tool` must succeed before apply |
| I10 | No fake test PASS | Tests must run real code or be marked `xfail` |

## Threat model

### In scope
- Malicious dynamic tool code (auto-created by agent)
- Prompt injection in user input
- Tool result injection (LLM returns crafted output)
- Network exfiltration via DNS / outbound HTTP
- File system escape via `..` / symlinks / absolute paths
- Privilege escalation via shell tools

### Out of scope
- Compromised LLM provider API (we trust the provider)
- Physical access to the host machine
- Side-channel attacks (timing, cache)
- Adversarial ML on the LLM itself (jailbreaks)

## Security features

### Authorization
- `WorkspacePolicy` restricts file ops to allow-listed roots
- `WebAllowList` restricts network to allow-listed domains
- RBAC: per-tool requires_confirm flag
- HMAC-chained audit trail (every AuthDecision signed)

### Sandboxing
- All side-effecting tools require explicit confirmation
- Dynamic tool execution goes through `check_safety` AST
- Browser tools run in headless Chromium with `ignore_https_errors` for testing only
- LLM prompts framed via PAM (Prompt Anti-injection Module)

### Monitoring
- Audit trail written to `<data_dir>/audit.db`
- All security events logged
- Anomaly detection on tool call patterns

## Best practices for deployment

1. **Run as non-root** (ODC refuses to run as root in production)
2. **Use environment variables** for API keys, not hard-coded
3. **Enable audit logging** in production
4. **Restrict workspace roots** to directories you control
5. **Keep `require_safety=true`** (the default)
6. **Use a firewall** to limit outbound to LLM API endpoints
7. **Review audit logs** regularly
8. **Update regularly** (we patch CVEs)

## Known limitations

- Browser tools can navigate to any URL (allow-list not enforced on browser)
- LLM output is trusted (the model is the trust boundary)
- No formal verification (we test, but don't prove invariants)
- Custom code execution paths (WASM, native) not yet sandboxed

## Hall of fame

We thank the following for responsible disclosure:

(none yet — be the first!)
