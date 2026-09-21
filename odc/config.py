"""ODC configuration.

Loads from env vars and an optional YAML file. The pattern is: env wins,
file is the default, hard-coded fallback is the last resort.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()  # safe no-op if no .env


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    """Runtime configuration for the ODC agent.

    One config object, passed everywhere. No module-level globals, no
    hidden state. Override at construction time for tests.
    """

    # LLM
    llm_provider: str = field(default_factory=lambda: _env("ODC_LLM_PROVIDER", "anthropic"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(
        default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
    )
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o-mini"))
    ollama_host: str = field(
        default_factory=lambda: _env("OLLAMA_HOST", "http://localhost:11434")
    )
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5:14b"))

    # NVIDIA (OpenAI-compatible at https://integrate.api.nvidia.com/v1)
    nvidia_api_key: str = field(default_factory=lambda: _env("NVIDIA_API_KEY"))
    nvidia_base_url: str = field(
        default_factory=lambda: _env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    )
    nvidia_model: str = field(
        default_factory=lambda: _env("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")
    )

    # Loop
    max_loop_turns: int = field(default_factory=lambda: _env_int("ODC_MAX_LOOP_TURNS", 25))
    max_retries: int = field(default_factory=lambda: _env_int("ODC_MAX_RETRIES", 3))
    verify_hard_cap: int = field(default_factory=lambda: _env_int("ODC_VERIFY_HARD_CAP", 3))
    lookup_budget: int = field(default_factory=lambda: _env_int("ODC_LOOKUP_BUDGET", 2))

    # Tools
    tool_web_enabled: bool = field(default_factory=lambda: _env_bool("ODC_TOOL_WEB_ENABLED", True))
    tool_shell_enabled: bool = field(
        default_factory=lambda: _env_bool("ODC_TOOL_SHELL_ENABLED", True)
    )
    tool_browser_enabled: bool = field(
        default_factory=lambda: _env_bool("ODC_TOOL_BROWSER_ENABLED", False)
    )
    tool_reach_enabled: bool = field(default_factory=lambda: _env_bool("ODC_TOOL_REACH_ENABLED", False))
    shell_allowlist: list[str] = field(
        default_factory=lambda: [
            cmd.strip()
            for cmd in _env("ODC_SHELL_ALLOWLIST", "").split(",")
            if cmd.strip()
        ]
    )

    # Storage
    log_level: str = field(default_factory=lambda: _env("ODC_LOG_LEVEL", "INFO"))
    data_dir: Path = field(default_factory=lambda: Path(_env("ODC_DATA_DIR", "./.odc_data")))

    # ----- derived -----
    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "memory").mkdir(exist_ok=True)
        (self.data_dir / "skills" / "learned").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(exist_ok=True)

    # ----- to/from dict -----
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Path):
                d[k] = str(v)
            else:
                d[k] = v
        return d


def load_config(path: str | Path | None = None) -> Config:
    """Load config: defaults → YAML file (if given) → env vars win."""
    cfg = Config()
    if path and Path(path).exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for k, v in data.items():
            if hasattr(cfg, k):
                if k == "data_dir":
                    setattr(cfg, k, Path(v))
                else:
                    setattr(cfg, k, v)
    return cfg
