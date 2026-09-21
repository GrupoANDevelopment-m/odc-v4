"""Shared pytest fixtures + setup for ODC v4.

The e2e tests need a live LLM provider. The unit tests don't.
This conftest sets up the provider only when an e2e test runs.
"""
import os
import sys
from pathlib import Path

import pytest

# Make the project root importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _has_nvidia_key() -> bool:
    key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("nvapi")
    if key:
        return True
    for envfile in (ROOT / ".env", Path("/workspace/.env")):
        if envfile.exists():
            for line in envfile.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if "nvidia" in k.lower() and v.strip():
                    os.environ[k.strip()] = v.strip()
                    return True
    return False


def pytest_collection_modifyitems(config, items):
    """Mark e2e tests so they only run when explicitly requested
    OR when a key is available. Unit tests run always."""
    has_key = _has_nvidia_key()
    skip_e2e = pytest.mark.skip(reason="no NVIDIA_API_KEY in env")
    for item in items:
        if "test_deep_reason_e2e" in str(item.fspath) or "test_e2e_dynamic_prompt" in str(item.fspath):
            if not has_key:
                item.add_marker(skip_e2e)


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Per-test isolation: clear the cognitive profile + checkpoint DB +
    knowledge base before each test. Without this, tests bleed state
    (the loop's "resume from checkpoint" picks up previous messages).
    """
    # Point all data at tmp_path so tests don't share a global DB
    monkeypatch.setenv("ODC_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    # Clean any pre-existing state files in cwd (legacy)
    for stale in (".odc_data", "checkpoints.db"):
        p = Path.cwd() / stale
        if p.exists():
            import shutil
            shutil.rmtree(p, ignore_errors=True)
    yield
    # Reset module-level cognitive provider/profile so next test is fresh
    try:
        from odc.cognitive import tools as _cogtools
        _cogtools._PROFILE = None
        _cogtools._PATHS.clear()
    except Exception:
        pass


def pytest_configure(config):
    """Set up the LLM provider at session start if a key is available.
    No-op if no key."""
    if not _has_nvidia_key():
        return
    try:
        from odc.config import load_config
        from odc.llm.provider import NvidiaProvider
        from odc.cognitive import tools as _cogtools
        cfg = load_config()
        _cogtools.LLM_PROVIDER = NvidiaProvider(cfg)
    except Exception as e:
        print(f"[conftest] provider setup failed: {e}")
