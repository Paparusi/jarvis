"""Tests for LLM response models."""

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


class TestLLMResponse:
    def test_basic_text_response(self):
        resp = LLMResponse(
            choices=[Choice(message=Message(content="Hello world"))],
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            model="claude-sonnet-4-20250514",
        )
        assert resp.choices[0].message.content == "Hello world"
        assert resp.choices[0].finish_reason == "stop"
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.completion_tokens == 5

    def test_defaults(self):
        resp = LLMResponse()
        assert resp.choices == []
        assert resp.usage is None
        assert resp.model == ""

    def test_tool_calls_response(self):
        tc = ToolCall(
            id="call_123",
            function=FunctionCall(name="web_search", arguments='{"query": "test"}'),
        )
        msg = Message(content="", tool_calls=[tc])
        resp = LLMResponse(
            choices=[Choice(message=msg, finish_reason="tool_calls")],
        )
        assert resp.choices[0].message.tool_calls[0].id == "call_123"
        assert resp.choices[0].message.tool_calls[0].function.name == "web_search"
        assert resp.choices[0].message.tool_calls[0].function.arguments == '{"query": "test"}'
        assert resp.choices[0].finish_reason == "tool_calls"

    def test_stream_chunk(self):
        chunk = StreamChunkResponse(
            choices=[StreamChoice(delta=DeltaMessage(content="Hi"))],
            model="claude-sonnet-4-20250514",
        )
        assert chunk.choices[0].delta.content == "Hi"
        assert chunk.choices[0].finish_reason is None

    def test_usage_total(self):
        usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert usage.total_tokens == 150
