"""Claude API client — wraps Anthropic Python SDK.

Accepts OpenAI-format messages/tools and returns OpenAI-compatible response
objects (LLMResponse / StreamChunkResponse).  All translation between formats
happens internally so the rest of the codebase can stay format-agnostic.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncIterator

import anthropic

from src.intelligence.llm_models import (
    Choice,
    DeltaMessage,
    FunctionCall,
    LLMResponse,
    Message,
    StreamChoice,
    StreamChunkResponse,
    ToolCall,
    Usage,
)
from src.utils.logging import get_logger

log = get_logger("intelligence.claude_client")

DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 4096


# ---------------------------------------------------------------------------
# ClaudeClient
# ---------------------------------------------------------------------------


class ClaudeClient:
    """Async client for the Anthropic Claude API.

    Translates OpenAI-format messages/tools to Claude's native format and
    maps responses back to the OpenAI-compatible dataclasses from llm_models.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or DEFAULT_MODEL
        self._client = anthropic.AsyncAnthropic(api_key=self.api_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion and return an LLMResponse."""
        model = model or self.model
        system_text = self._extract_system(messages)
        translated = self._translate_messages(messages)

        # JSON mode: nudge via system prompt
        if response_format and response_format.get("type") == "json_object":
            suffix = "\n\nIMPORTANT: You must respond with valid JSON only, no other text."
            system_text = (system_text + suffix) if system_text else suffix.lstrip("\n")

        api_kwargs: dict[str, Any] = {
            "model": model,
            "messages": translated,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_text:
            api_kwargs["system"] = system_text

        claude_tools = self._translate_tools(tools)
        if claude_tools is not None:
            api_kwargs["tools"] = claude_tools
            tc = self._translate_tool_choice(tool_choice)
            if tc is not None:
                api_kwargs["tool_choice"] = tc

        try:
            response = await self._client.messages.create(**api_kwargs)
        except Exception as exc:
            log.error("claude_api_error", model=model, error=str(exc))
            raise

        result = self._translate_response(response)
        log.info(
            "claude_response",
            model=model,
            tokens_in=result.usage.prompt_tokens if result.usage else 0,
            tokens_out=result.usage.completion_tokens if result.usage else 0,
        )
        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunkResponse]:
        """Stream a chat completion, yielding StreamChunkResponse chunks."""
        model = model or self.model
        system_text = self._extract_system(messages)
        translated = self._translate_messages(messages)

        api_kwargs: dict[str, Any] = {
            "model": model,
            "messages": translated,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_text:
            api_kwargs["system"] = system_text

        try:
            async with self._client.messages.stream(**api_kwargs) as stream:
                async for text in stream.text_stream:
                    yield StreamChunkResponse(
                        choices=[
                            StreamChoice(
                                delta=DeltaMessage(content=text, role="assistant"),
                            )
                        ],
                        model=model,
                    )

                final = await stream.get_final_message()
                usage = Usage(
                    prompt_tokens=final.usage.input_tokens,
                    completion_tokens=final.usage.output_tokens,
                    total_tokens=final.usage.input_tokens + final.usage.output_tokens,
                )
                yield StreamChunkResponse(
                    choices=[
                        StreamChoice(
                            delta=DeltaMessage(content=""),
                            finish_reason="stop",
                        )
                    ],
                    usage=usage,
                    model=model,
                )
        except Exception as exc:
            log.error("claude_stream_error", model=model, error=str(exc))
            raise

    # ------------------------------------------------------------------
    # Message translation
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_system(messages: list[dict[str, Any]]) -> str:
        """Pull out all system messages and merge into one string."""
        parts: list[str] = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if content:
                    parts.append(content)
        return "\n\n".join(parts)

    @staticmethod
    def _translate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate OpenAI-format messages to Claude-format messages.

        Handles:
        - Stripping system messages (handled separately)
        - assistant tool_calls → tool_use content blocks
        - tool role messages → merged tool_result content blocks in a user msg
        - user multimodal (image_url) → Claude image blocks
        - plain user/assistant text → kept as-is
        """
        result: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def _flush_tool_results() -> None:
            if pending_tool_results:
                result.append({
                    "role": "user",
                    "content": list(pending_tool_results),
                })
                pending_tool_results.clear()

        for msg in messages:
            role = msg.get("role", "")

            # Skip system (extracted separately)
            if role == "system":
                continue

            # Tool result messages: accumulate consecutive ones
            if role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
                continue

            # Flush any pending tool results before a non-tool message
            _flush_tool_results()

            # Assistant with tool_calls → convert to tool_use content blocks
            if role == "assistant" and msg.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                # Include any text content first
                text_content = msg.get("content")
                if text_content:
                    blocks.append({"type": "text", "text": text_content})
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    raw_input = func.get("arguments", "{}")
                    try:
                        parsed_input = json.loads(raw_input) if isinstance(raw_input, str) else raw_input
                    except (json.JSONDecodeError, TypeError):
                        parsed_input = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": parsed_input,
                    })
                result.append({"role": "assistant", "content": blocks})
                continue

            # User with array content (multimodal)
            if role == "user" and isinstance(msg.get("content"), list):
                translated_parts: list[dict[str, Any]] = []
                for part in msg["content"]:
                    if part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        # Parse data URI: data:{mime};base64,{data}
                        m = re.match(r"data:([^;]+);base64,(.+)", url, re.DOTALL)
                        if m:
                            translated_parts.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": m.group(1),
                                    "data": m.group(2),
                                },
                            })
                        else:
                            # URL-based image — pass as url source
                            translated_parts.append({
                                "type": "image",
                                "source": {
                                    "type": "url",
                                    "url": url,
                                },
                            })
                    elif part.get("type") == "text":
                        # text blocks are kept as-is (Claude understands them)
                        translated_parts.append(part)
                    else:
                        translated_parts.append(part)
                result.append({"role": "user", "content": translated_parts})
                continue

            # Plain user/assistant text
            result.append({"role": role, "content": msg.get("content", "")})

        # Flush any trailing tool results
        _flush_tool_results()

        return result

    # ------------------------------------------------------------------
    # Tool translation
    # ------------------------------------------------------------------

    @staticmethod
    def _translate_tools(
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        """Translate OpenAI tool definitions to Claude format."""
        if not tools:
            return None

        claude_tools: list[dict[str, Any]] = []
        for tool in tools:
            func = tool.get("function", {})
            claude_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {}),
            })
        return claude_tools

    @staticmethod
    def _translate_tool_choice(
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Translate OpenAI tool_choice to Claude format."""
        if tool_choice is None or tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "none":
            return None
        if tool_choice == "required":
            return {"type": "any"}
        if isinstance(tool_choice, dict):
            return tool_choice
        return {"type": "auto"}

    # ------------------------------------------------------------------
    # Response translation
    # ------------------------------------------------------------------

    @staticmethod
    def _translate_response(response: Any) -> LLMResponse:
        """Translate a Claude API response to an LLMResponse."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        function=FunctionCall(
                            name=block.name,
                            arguments=json.dumps(block.input),
                        ),
                    )
                )

        content_text = "".join(text_parts) if text_parts else None

        # Map stop_reason
        stop_map = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
        }
        finish_reason = stop_map.get(response.stop_reason, response.stop_reason or "stop")

        return LLMResponse(
            choices=[
                Choice(
                    message=Message(
                        content=content_text,
                        role="assistant",
                        tool_calls=tool_calls if tool_calls else None,
                    ),
                    finish_reason=finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            ),
            model=response.model,
            id=response.id,
        )


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

_client: ClaudeClient | None = None


def get_claude_client() -> ClaudeClient:
    """Return the global ClaudeClient singleton (lazy-created)."""
    global _client
    if _client is None:
        _client = ClaudeClient()
    return _client


def reset_claude_client() -> None:
    """Reset the singleton so the next call creates a fresh instance."""
    global _client
    _client = None
