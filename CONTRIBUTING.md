# Contributing to ODC v4

ODC v4 is a self-improving agent. We welcome contributions that:
- Add real capabilities (no fake/mock features)
- Pass existing tests AND add new tests
- Don't break the constitutional guard
- Stay honest about limitations

## Quick start

```bash
git clone https://github.com/GrupoANDevelopment-m/odc-v4.git
cd odc-v4
./setup.sh
# Edit .env with your API keys
make test
```

## Development workflow

1. **Branch off main**
   ```bash
   git checkout -b feat/your-feature
   ```

2. **Make changes** in `odc/`, `tests/`, or `docs/`

3. **Run the full check suite**
   ```bash
   make check
   ```
   This runs: lint, type-check, unit tests.

4. **Add tests** for new functionality
   - Unit tests in `tests/test_<module>.py`
   - Integration tests marked with `@pytest.mark.integration`
   - LLM-dependent tests marked with `@pytest.mark.llm`

5. **Run audit** if you changed cognitive/refinement modules
   ```bash
   make audit
   ```

6. **Commit with conventional commits**
   ```
   feat: add new self-heal strategy for ImportError
   fix: SQLite WAL deadlock under 50 threads
   docs: update self-heal report with real LLM
   test: cross-process browser persistence
   ```

7. **Open a PR** against `main`
   - Title: short imperative
   - Body: what + why + how verified

## Code style

- **Python 3.11+** required
- **Line length:** 100
- **Formatter:** black + ruff
- **Type hints:** encouraged, not required (gradual typing)
- **Docstrings:** Google-style

```python
def example(name: str, count: int = 0) -> dict:
    """Do something.

    Args:
        name: The name.
        count: How many times.

    Returns:
        A dict with results.
    """
    return {"name": name, "count": count}
```

## Architecture rules

### Constitutional invariants (NEVER break)

The agent enforces 10 invariants (I1-I10) on all self-modifications.
Your code must respect them:

- **I1:** No `os.system`, `subprocess.Popen(shell=True)`
- **I2:** No unkeyed network calls outside allow-list
- **I3:** No file writes outside workspace roots
- **I4:** `require_safety` cannot be disabled
- **I5:** Audit trail must remain append-only
- **I6:** Cognitive profile must remain thread-safe
- **I7:** No bypassing the constitutional guard
- **I8:** No bypassing the rate limit
- **I9:** No bypassing the sandbox test
- **I10:** No fake "PASS" in test verdicts

### Test honesty

Tests must be REAL. If a test requires network/LLM/GPU:
- Mark it with the appropriate pytest marker
- Skip it gracefully if the resource is unavailable
- Never silently pass when the test was skipped

Bad:
```python
def test_real_llm():
    response = mock_llm()  # never called the real LLM
    assert response.text  # trivially true
```

Good:
```python
@pytest.mark.llm
def test_real_llm():
    response = real_llm.ask_sync("...")
    assert "expected" in response.text
```

### Safety-critical changes

If you change:
- Constitutional guard (`odc/governance/`)
- Auto-extend gate (`odc/code/auto_extend.py`)
- Self-heal engine (`odc/refinement/self_heal.py`)
- Authorization (`odc/security/`)

You MUST:
- Add a new audit test in `tests/test_deep_audit.py`
- Update the relevant docs in `docs/`
- Get sign-off from a maintainer

## Reporting bugs

Use GitHub Issues. Include:
- ODC v4 version
- Python version
- OS
- Minimal reproduction
- Expected vs actual

## License

By contributing, you agree your work is MIT-licensed (same as the project).
