"""LLM provider abstraction: one interface, four implementations.

Pick the one that matches your keys: anthropic, openai, ollama, or nvidia.
"""
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
]
