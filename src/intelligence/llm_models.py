"""LLM Response Models — OpenAI-compatible dataclasses.

Unified response format used by both ClaudeClient and OllamaClient.
Replaces litellm's response objects with simple dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FunctionCall:
    """Function call info within a tool call."""
    name: str = ""
    arguments: str = "{}"  # JSON string


@dataclass
class ToolCall:
    """A single tool call from the LLM."""
    id: str = ""
    type: str = "function"
    function: FunctionCall = field(default_factory=FunctionCall)


@dataclass
class Message:
    """LLM response message."""
    content: str | None = None
    role: str = "assistant"
    tool_calls: list[ToolCall] | None = None


@dataclass
class Choice:
    """A single choice in the LLM response."""
    message: Message = field(default_factory=Message)
    finish_reason: str = "stop"
    index: int = 0


@dataclass
class DeltaMessage:
    """Partial message for streaming chunks."""
    content: str | None = None
    role: str | None = None


@dataclass
class StreamChoice:
    """A single choice in a streaming chunk."""
    delta: DeltaMessage = field(default_factory=DeltaMessage)
    finish_reason: str | None = None
    index: int = 0


@dataclass
class Usage:
    """Token usage statistics."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """Complete LLM response — compatible with litellm/OpenAI format."""
    choices: list[Choice] = field(default_factory=list)
    usage: Usage | None = None
    model: str = ""
    id: str = ""


@dataclass
class StreamChunkResponse:
    """A single streaming chunk response."""
    choices: list[StreamChoice] = field(default_factory=list)
    usage: Usage | None = None
    model: str = ""
