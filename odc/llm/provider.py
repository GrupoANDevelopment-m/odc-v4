"""LLM provider abstraction.

One interface, three implementations. The agent never sees the wire
format — it sees a list of messages and gets text back.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
import re

from odc.config import Config
from odc.observability import get_logger, log_event

log = get_logger("odc.llm")


@dataclass
class Message:
    """One message in a conversation.

    role: 'system' | 'user' | 'assistant' | 'tool'
    content: text content (assistant may also carry tool_use)
    tool_call_id: when role=='tool', the id of the call this is the result for
    tool_calls: when role=='assistant', the tool calls the model wants to make
    name: optional name (used by some tool-role conventions)
    """

    role: str
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role}
        if self.content:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class ToolSpec:
    """JSON-Schema style tool description, normalized across providers."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON-Schema object

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class Completion:
    """Result of a chat call. tool_calls is filled when the model wants to use tools."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    usage: dict[str, int] = field(default_factory=dict)


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert internal Message list to OpenAI / NVIDIA wire format.

    Internal tool_calls are flat dicts: [{id, name, arguments}].
    OpenAI wire format requires: [{id, type: 'function', function: {name, arguments}}].
    Tool names are sanitized: dots become double-underscores.
    Tool messages need role='tool' (OpenAI 2.52 SDK enforces this).
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append(
                {
                    "role": "tool",
                    "content": m.content or "",
                    "tool_call_id": m.tool_call_id or "",
                }
            )
            continue
        d: dict[str, Any] = {"role": m.role}
        if m.content:
            d["content"] = m.content
        if m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": _to_wire_name(tc["name"]),
                        "arguments": json.dumps(tc.get("arguments") or {}),
                    },
                }
                for tc in m.tool_calls
            ]
        out.append(d)
    return out


# Tool name sanitization: OpenAI/NVIDIA require [a-zA-Z0-9_-] only.
# ODC uses dots as namespace separators (code.read, fs.write).
_WIRE_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _to_wire_name(name: str) -> str:
    """'code.read' -> 'code__read'."""
    return _WIRE_SAFE_RE.sub("__", name)


def _from_wire_name(name: str) -> str:
    """Reverse: 'code__read' -> 'code.read'."""
    return name.replace("__", ".")


class Provider(ABC):
    """Abstract LLM provider."""

    name: str = "abstract"

    def __init__(self, config: Config) -> None:
        self.config = config
        # Optional call log: each entry is (messages, tools) that was
        # passed to chat(). Useful for tests, debugging, prompt
        # observability, and prompt-compression research. Not all
        # providers populate this; subclasses can call _record_call()
        # at the start of their chat() implementations.
        self.calls: list[tuple[list, list | None]] = []

    def _record_call(self, messages: list, tools: list | None) -> None:
        """Record a chat() call. Default implementation; subclasses
        can override or just call this directly."""
        # Defensive shallow copy to avoid surprises if the caller
        # mutates the lists after the call.
        try:
            self.calls.append((list(messages), list(tools) if tools else None))
        except Exception:
            self.calls.append((messages, tools))

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Completion:
        """Send messages, get back text + any tool calls the model wants to make."""


# --- Anthropic -----------------------------------------------------------------

class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        if not config.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Install the 'anthropic' extra: pip install 'odc[anthropic]'"
            ) from e
        self._client = AsyncAnthropic(api_key=config.anthropic_api_key)
        self._model = config.anthropic_model

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Completion:
        self._record_call(messages, tools)
        # Anthropic wants system message separate; tool results in user turns.
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            elif m.role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc.get("arguments", {}),
                        }
                    )
                converted.append({"role": "assistant", "content": blocks})
            else:
                converted.append({"role": m.role, "content": m.content})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": converted,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if tools:
            kwargs["tools"] = [t.to_anthropic() for t in tools]

        resp = await self._client.messages.create(**kwargs)
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input,
                    }
                )
        return Completion(
            text="".join(text_parts),
            tool_calls=tool_calls,
            raw=resp,
            usage={
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
        )


# --- OpenAI --------------------------------------------------------------------

class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        if not config.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not set")
        try:
            from openai import AsyncOpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Install the 'openai' extra: pip install 'odc[openai]'"
            ) from e
        self._client = AsyncOpenAI(api_key=config.openai_api_key)
        self._model = config.openai_model

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Completion:
        converted = _to_openai_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": converted,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            wire_tools = []
            for t in tools:
                spec = t.to_openai()
                fn = spec.get("function", {})
                if "name" in fn:
                    fn["name"] = _to_wire_name(fn["name"])
                wire_tools.append(spec)
            kwargs["tools"] = wire_tools
            kwargs["tool_choice"] = "auto"
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = choice.message.content or ""
        tool_calls: list[dict[str, Any]] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": _from_wire_name(tc.function.name),
                        "arguments": args,
                    }
                )
        usage = {}
        if resp.usage:
            usage = {
                "input_tokens": resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
            }
        return Completion(text=text, tool_calls=tool_calls, raw=resp, usage=usage)


# --- Ollama (local, plain HTTP) ----------------------------------------------

class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._host = config.ollama_host.rstrip("/")
        self._model = config.ollama_model

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Completion:
        self._record_call(messages, tools)
        # Ollama's /api/chat supports tool calling for some models.
        converted = _to_openai_messages(messages)
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": converted,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self._host}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        msg = data.get("message", {})
        text = msg.get("content", "")
        tool_calls: list[dict[str, Any]] = []
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            try:
                args = fn.get("arguments")
                if isinstance(args, str):
                    args = json.loads(args)
                args = args or {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                {
                    "id": tc.get("id", f"call_{len(tool_calls)}"),
                    "name": fn.get("name", ""),
                    "arguments": args,
                }
            )
        return Completion(text=text, tool_calls=tool_calls, raw=data, usage={})


# --- NVIDIA (OpenAI-compatible via /v1/chat/completions) ---------------------

class NvidiaProvider(Provider):
    """NVIDIA Integrate API. Drop-in OpenAI-compatible client.

    Endpoint: https://integrate.api.nvidia.com/v1
    Auth:     Bearer $NVIDIA_API_KEY
    Models:   meta/llama-3.1-70b-instruct, mistralai/mixtral-8x7b-instruct-v0.1,
              google/gemma-2-9b-it, microsoft/phi-3-medium-4k-instruct, etc.
    """

    name = "nvidia"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        if not config.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is not set")
        try:
            from openai import AsyncOpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Install the 'openai' extra: pip install 'odc[openai]'"
            ) from e
        self._client = AsyncOpenAI(
            api_key=config.nvidia_api_key,
            base_url=config.nvidia_base_url,
        )
        self._model = config.nvidia_model

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Completion:
        self._record_call(messages, tools)
        converted = _to_openai_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": converted,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            wire_tools = []
            for t in tools:
                spec = t.to_openai()
                fn = spec.get("function", {})
                if "name" in fn:
                    fn["name"] = _to_wire_name(fn["name"])
                wire_tools.append(spec)
            kwargs["tools"] = wire_tools
            kwargs["tool_choice"] = "auto"
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = choice.message.content or ""
        tool_calls: list[dict[str, Any]] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": _from_wire_name(tc.function.name),
                        "arguments": args,
                    }
                )
        usage = {}
        if resp.usage:
            usage = {
                "input_tokens": resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
            }
        return Completion(text=text, tool_calls=tool_calls, raw=resp, usage=usage)


# --- Factory ------------------------------------------------------------------

_REGISTRY: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "nvidia": NvidiaProvider,
}


def get_provider(config: Config) -> Provider:
    name = (config.llm_provider or "").lower()
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown LLM provider: {name!r}. "
            f"Known: {sorted(_REGISTRY)}. "
            f"Set ODC_LLM_PROVIDER env var."
        )
    log_event(log, 20, "provider_init", provider=name, model=cls.__name__)
    return cls(config)


def list_providers() -> list[str]:
    return sorted(_REGISTRY)
