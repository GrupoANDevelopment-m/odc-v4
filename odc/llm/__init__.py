"""LLM provider abstraction: one interface, four implementations.

Pick the one that matches your keys: anthropic, openai, ollama, or nvidia.

Plus the LocalBrain manager — handles download, install, switch of
local Ollama-compatible models that act as the *secondary* brain
for the ODC agent.
"""
from odc.llm.local import (
    LocalBrain,
    ModelInfo,
    BrainState,
    probe,
    get_recommended,
    DEFAULT_BASE_URL,
)
from odc.llm.provider import (
    AnthropicProvider,
    Completion,
    Message,
    NvidiaProvider,
    OllamaProvider,
    OpenAIProvider,
    Provider,
    ToolSpec,
    get_provider,
    list_providers,
)

__all__ = [
    # Providers
    "AnthropicProvider",
    "Completion",
    "Message",
    "NvidiaProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "Provider",
    "ToolSpec",
    "get_provider",
    "list_providers",
    # Local Brain (secondary)
    "LocalBrain",
    "ModelInfo",
    "BrainState",
    "probe",
    "get_recommended",
    "DEFAULT_BASE_URL",
]
