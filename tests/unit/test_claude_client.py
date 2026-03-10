"""Tests for ClaudeClient — message/tool/response translation and singleton."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.intelligence.claude_client import (
    ClaudeClient,
    get_claude_client,
    reset_claude_client,
)
from src.intelligence.llm_models import LLMResponse, StreamChunkResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> ClaudeClient:
    """Create a ClaudeClient with a dummy key (no real API calls)."""
    return ClaudeClient(api_key="sk-ant-test-key")


# ---------------------------------------------------------------------------
# TestExtractSystem
# ---------------------------------------------------------------------------


class TestExtractSystem:
    """Tests for ClaudeClient._extract_system."""

    def test_system_extracted(self) -> None:
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        result = ClaudeClient._extract_system(messages)
        assert result == "You are helpful."

    def test_no_system(self) -> None:
        messages = [
            {"role": "user", "content": "Hi"},
        ]
        result = ClaudeClient._extract_system(messages)
        assert result == ""

    def test_multiple_system_merged(self) -> None:
        messages = [
            {"role": "system", "content": "Part one."},
            {"role": "system", "content": "Part two."},
            {"role": "user", "content": "Hi"},
        ]
        result = ClaudeClient._extract_system(messages)
        assert result == "Part one.\n\nPart two."


# ---------------------------------------------------------------------------
# TestTranslateMessages
# ---------------------------------------------------------------------------


class TestTranslateMessages:
    """Tests for ClaudeClient._translate_messages."""

    def test_user_text(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        result = ClaudeClient._translate_messages(messages)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_assistant_text(self) -> None:
        messages = [{"role": "assistant", "content": "Sure!"}]
        result = ClaudeClient._translate_messages(messages)
        assert result == [{"role": "assistant", "content": "Sure!"}]

    def test_tool_calls_to_tool_use(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query": "weather"}',
                        },
                    }
                ],
            }
        ]
        result = ClaudeClient._translate_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        blocks = result[0]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["id"] == "tc_1"
        assert blocks[0]["name"] == "web_search"
        assert blocks[0]["input"] == {"query": "weather"}

    def test_tool_results_merged(self) -> None:
        messages = [
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "content": "Result A",
            },
            {
                "role": "tool",
                "tool_call_id": "tc_2",
                "content": "Result B",
            },
        ]
        result = ClaudeClient._translate_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        content = result[0]["content"]
        assert len(content) == 2
        assert content[0] == {
            "type": "tool_result",
            "tool_use_id": "tc_1",
            "content": "Result A",
        }
        assert content[1] == {
            "type": "tool_result",
            "tool_use_id": "tc_2",
            "content": "Result B",
        }

    def test_multimodal_image(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgo="
                        },
                    },
                ],
            }
        ]
        result = ClaudeClient._translate_messages(messages)
        assert len(result) == 1
        content = result[0]["content"]
        assert len(content) == 2
        assert content[0] == {"type": "text", "text": "What is this?"}
        assert content[1] == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "iVBORw0KGgo=",
            },
        }

    def test_mixed_conversation_chain(self) -> None:
        """Full agent loop: system -> user -> assistant(tool_calls) -> tool -> tool -> assistant."""
        messages = [
            {"role": "system", "content": "You are JARVIS."},
            {"role": "user", "content": "Search for weather"},
            {
                "role": "assistant",
                "content": "Let me search.",
                "tool_calls": [
                    {
                        "id": "tc_w",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"q": "weather"}',
                        },
                    },
                    {
                        "id": "tc_t",
                        "function": {
                            "name": "get_time",
                            "arguments": "{}",
                        },
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "tc_w", "content": "Sunny 25C"},
            {"role": "tool", "tool_call_id": "tc_t", "content": "14:00"},
            {"role": "assistant", "content": "It is sunny, 25C at 14:00."},
        ]
        result = ClaudeClient._translate_messages(messages)
        # System stripped, so 4 messages: user, assistant(tool_use), user(tool_results), assistant
        assert len(result) == 4

        # 1. user text
        assert result[0] == {"role": "user", "content": "Search for weather"}

        # 2. assistant with tool_use blocks (text + 2 tool_use)
        assert result[1]["role"] == "assistant"
        blocks = result[1]["content"]
        assert len(blocks) == 3
        assert blocks[0] == {"type": "text", "text": "Let me search."}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["name"] == "web_search"
        assert blocks[2]["type"] == "tool_use"
        assert blocks[2]["name"] == "get_time"

        # 3. merged tool results
        assert result[2]["role"] == "user"
        assert len(result[2]["content"]) == 2
        assert result[2]["content"][0]["tool_use_id"] == "tc_w"
        assert result[2]["content"][1]["tool_use_id"] == "tc_t"

        # 4. final assistant text
        assert result[3] == {
            "role": "assistant",
            "content": "It is sunny, 25C at 14:00.",
        }

    def test_system_messages_stripped(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Hi"},
        ]
        result = ClaudeClient._translate_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_user_list_content_text_only(self) -> None:
        """User message with list content containing only text blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": "World"},
                ],
            }
        ]
        result = ClaudeClient._translate_messages(messages)
        assert result[0]["content"] == [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"},
        ]


# ---------------------------------------------------------------------------
# TestTranslateTools
# ---------------------------------------------------------------------------


class TestTranslateTools:
    """Tests for ClaudeClient._translate_tools."""

    def test_openai_to_claude(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                },
            }
        ]
        result = ClaudeClient._translate_tools(tools)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "web_search"
        assert result[0]["description"] == "Search the web"
        assert result[0]["input_schema"]["type"] == "object"
        assert "query" in result[0]["input_schema"]["properties"]

    def test_empty_tools(self) -> None:
        assert ClaudeClient._translate_tools(None) is None
        assert ClaudeClient._translate_tools([]) is None


# ---------------------------------------------------------------------------
# TestTranslateToolChoice
# ---------------------------------------------------------------------------


class TestTranslateToolChoice:
    """Tests for ClaudeClient._translate_tool_choice."""

    def test_auto(self) -> None:
        assert ClaudeClient._translate_tool_choice("auto") == {"type": "auto"}

    def test_none(self) -> None:
        assert ClaudeClient._translate_tool_choice("none") is None

    def test_required(self) -> None:
        assert ClaudeClient._translate_tool_choice("required") == {"type": "any"}

    def test_dict_passthrough(self) -> None:
        custom = {"type": "tool", "name": "web_search"}
        assert ClaudeClient._translate_tool_choice(custom) == custom

    def test_none_value(self) -> None:
        assert ClaudeClient._translate_tool_choice(None) == {"type": "auto"}


# ---------------------------------------------------------------------------
# TestTranslateResponse
# ---------------------------------------------------------------------------


def _mock_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _mock_tool_use_block(
    tool_id: str, name: str, input_data: dict
) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_data
    return block


def _mock_usage(input_tokens: int = 10, output_tokens: int = 5) -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    return usage


def _mock_response(
    content: list | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
    model: str = "claude-sonnet-4-20250514",
    msg_id: str = "msg_123",
) -> MagicMock:
    mock_msg = MagicMock()
    mock_msg.content = content or [_mock_text_block("Hello")]
    mock_msg.stop_reason = stop_reason
    mock_msg.usage = _mock_usage(input_tokens, output_tokens)
    mock_msg.model = model
    mock_msg.id = msg_id
    return mock_msg


class TestTranslateResponse:
    """Tests for ClaudeClient._translate_response."""

    def test_text_only(self) -> None:
        mock = _mock_response(content=[_mock_text_block("Hello world")])
        result = ClaudeClient._translate_response(mock)

        assert isinstance(result, LLMResponse)
        assert result.choices[0].message.content == "Hello world"
        assert result.choices[0].message.tool_calls is None
        assert result.choices[0].finish_reason == "stop"

    def test_tool_use(self) -> None:
        mock = _mock_response(
            content=[
                _mock_tool_use_block("tu_1", "web_search", {"query": "test"}),
            ],
            stop_reason="tool_use",
        )
        result = ClaudeClient._translate_response(mock)

        assert result.choices[0].message.content is None
        tc = result.choices[0].message.tool_calls
        assert tc is not None
        assert len(tc) == 1
        assert tc[0].id == "tu_1"
        assert tc[0].function.name == "web_search"
        assert json.loads(tc[0].function.arguments) == {"query": "test"}
        assert result.choices[0].finish_reason == "tool_calls"

    def test_mixed_content(self) -> None:
        mock = _mock_response(
            content=[
                _mock_text_block("Let me search."),
                _mock_tool_use_block("tu_2", "browse", {"url": "https://x.com"}),
            ],
            stop_reason="tool_use",
        )
        result = ClaudeClient._translate_response(mock)

        assert result.choices[0].message.content == "Let me search."
        tc = result.choices[0].message.tool_calls
        assert tc is not None
        assert len(tc) == 1
        assert tc[0].function.name == "browse"

    def test_usage_mapping(self) -> None:
        mock = _mock_response(input_tokens=100, output_tokens=50)
        result = ClaudeClient._translate_response(mock)

        assert result.usage is not None
        assert result.usage.prompt_tokens == 100
        assert result.usage.completion_tokens == 50
        assert result.usage.total_tokens == 150

    def test_stop_reason_end_turn(self) -> None:
        mock = _mock_response(stop_reason="end_turn")
        result = ClaudeClient._translate_response(mock)
        assert result.choices[0].finish_reason == "stop"

    def test_stop_reason_tool_use(self) -> None:
        mock = _mock_response(stop_reason="tool_use")
        result = ClaudeClient._translate_response(mock)
        assert result.choices[0].finish_reason == "tool_calls"

    def test_stop_reason_max_tokens(self) -> None:
        mock = _mock_response(stop_reason="max_tokens")
        result = ClaudeClient._translate_response(mock)
        assert result.choices[0].finish_reason == "length"

    def test_model_and_id(self) -> None:
        mock = _mock_response(model="claude-opus-4-20250514", msg_id="msg_abc")
        result = ClaudeClient._translate_response(mock)
        assert result.model == "claude-opus-4-20250514"
        assert result.id == "msg_abc"


# ---------------------------------------------------------------------------
# TestComplete (async, mocked)
# ---------------------------------------------------------------------------


class TestComplete:
    """Tests for ClaudeClient.complete — mocked API calls."""

    @pytest.mark.asyncio
    async def test_complete_basic(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(
            content=[_mock_text_block("Hi there!")],
            stop_reason="end_turn",
        )
        client._client.messages.create = AsyncMock(return_value=mock_resp)

        result = await client.complete(
            messages=[{"role": "user", "content": "Hello"}],
        )

        assert isinstance(result, LLMResponse)
        assert result.choices[0].message.content == "Hi there!"
        client._client.messages.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complete_with_system(self) -> None:
        client = _make_client()
        mock_resp = _mock_response()
        client._client.messages.create = AsyncMock(return_value=mock_resp)

        await client.complete(
            messages=[
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hi"},
            ],
        )

        call_kwargs = client._client.messages.create.call_args[1]
        assert call_kwargs["system"] == "Be brief."
        # system should not appear in messages
        for msg in call_kwargs["messages"]:
            assert msg["role"] != "system"

    @pytest.mark.asyncio
    async def test_complete_with_tools(self) -> None:
        client = _make_client()
        mock_resp = _mock_response()
        client._client.messages.create = AsyncMock(return_value=mock_resp)

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        await client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
            tool_choice="auto",
        )

        call_kwargs = client._client.messages.create.call_args[1]
        assert call_kwargs["tools"][0]["name"] == "search"
        assert call_kwargs["tool_choice"] == {"type": "auto"}

    @pytest.mark.asyncio
    async def test_complete_json_mode(self) -> None:
        client = _make_client()
        mock_resp = _mock_response()
        client._client.messages.create = AsyncMock(return_value=mock_resp)

        await client.complete(
            messages=[
                {"role": "system", "content": "System prompt."},
                {"role": "user", "content": "Give JSON"},
            ],
            response_format={"type": "json_object"},
        )

        call_kwargs = client._client.messages.create.call_args[1]
        assert "valid JSON only" in call_kwargs["system"]
        assert call_kwargs["system"].startswith("System prompt.")

    @pytest.mark.asyncio
    async def test_complete_json_mode_no_system(self) -> None:
        client = _make_client()
        mock_resp = _mock_response()
        client._client.messages.create = AsyncMock(return_value=mock_resp)

        await client.complete(
            messages=[{"role": "user", "content": "Give JSON"}],
            response_format={"type": "json_object"},
        )

        call_kwargs = client._client.messages.create.call_args[1]
        assert "valid JSON only" in call_kwargs["system"]

    @pytest.mark.asyncio
    async def test_complete_tool_choice_none_skips_tools(self) -> None:
        client = _make_client()
        mock_resp = _mock_response()
        client._client.messages.create = AsyncMock(return_value=mock_resp)

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search",
                    "parameters": {},
                },
            }
        ]
        await client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
            tool_choice="none",
        )

        call_kwargs = client._client.messages.create.call_args[1]
        # tool_choice=none means don't send tools at all
        assert "tool_choice" not in call_kwargs


# ---------------------------------------------------------------------------
# TestStream (async, mocked)
# ---------------------------------------------------------------------------


class TestStream:
    """Tests for ClaudeClient.stream — mocked streaming."""

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self) -> None:
        client = _make_client()

        # Mock the streaming context manager
        mock_stream = AsyncMock()

        # Create an async iterator for text_stream
        async def _text_iter():
            yield "Hello "
            yield "world"

        mock_stream.text_stream = _text_iter()

        final_msg = MagicMock()
        final_msg.usage = _mock_usage(20, 10)
        mock_stream.get_final_message = AsyncMock(return_value=final_msg)

        # Make the context manager return our mock stream
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        client._client.messages.stream = MagicMock(return_value=mock_ctx)

        chunks: list[StreamChunkResponse] = []
        async for chunk in client.stream(
            messages=[{"role": "user", "content": "Hi"}],
        ):
            chunks.append(chunk)

        # "Hello ", "world", final usage chunk
        assert len(chunks) == 3
        assert chunks[0].choices[0].delta.content == "Hello "
        assert chunks[1].choices[0].delta.content == "world"
        # Final chunk has usage
        assert chunks[2].usage is not None
        assert chunks[2].usage.prompt_tokens == 20
        assert chunks[2].usage.completion_tokens == 10
        assert chunks[2].choices[0].finish_reason == "stop"


# ---------------------------------------------------------------------------
# TestSingleton
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for get_claude_client / reset_claude_client singleton pattern."""

    def setup_method(self) -> None:
        reset_claude_client()

    def teardown_method(self) -> None:
        reset_claude_client()

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    def test_get_returns_same_instance(self) -> None:
        a = get_claude_client()
        b = get_claude_client()
        assert a is b

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    def test_reset_creates_new_instance(self) -> None:
        a = get_claude_client()
        reset_claude_client()
        b = get_claude_client()
        assert a is not b
