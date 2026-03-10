"""Tests for Agent Loop — multi-step tool calling with retry, parallel execution,
clean serialization, and truncation detection."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

from src.gateway.models import AgentResponse, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.tools.base import ToolDefinition, ToolParameter, ToolRegistry, ToolResult


# --- Test helpers ---

@dataclass
class MockFunction:
    name: str = ""
    arguments: str = "{}"


@dataclass
class MockToolCall:
    id: str = "call_1"
    function: MockFunction = field(default_factory=MockFunction)


@dataclass
class MockMessage:
    content: str = ""
    tool_calls: list = field(default_factory=list)


@dataclass
class MockChoice:
    message: MockMessage = field(default_factory=MockMessage)
    finish_reason: str = "end_turn"


@dataclass
class MockUsage:
    prompt_tokens: int = 100
    completion_tokens: int = 50


@dataclass
class MockResponse:
    choices: list = field(default_factory=list)
    usage: MockUsage = field(default_factory=MockUsage)


def make_response(content="Hello!", tool_calls=None, finish_reason="end_turn"):
    msg = MockMessage(content=content, tool_calls=tool_calls or [])
    choice = MockChoice(message=msg, finish_reason=finish_reason)
    return MockResponse(choices=[choice])


def make_tool_response(tool_name="test_tool", args='{"query":"test"}', tool_id="call_1"):
    tc = MockToolCall(id=tool_id, function=MockFunction(name=tool_name, arguments=args))
    return make_response(content="", tool_calls=[tc])


@pytest.fixture
def tool_registry():
    """Create a registry with a mock tool."""
    registry = ToolRegistry()
    registry._event_bus = MagicMock()
    registry._event_bus.publish = AsyncMock()

    async def mock_handler(query: str = "test") -> ToolResult:
        return ToolResult(success=True, output=f"Result for: {query}")

    tool = ToolDefinition(
        name="test_tool",
        description="A test tool",
        parameters=[
            ToolParameter(name="query", type="string", description="Search query"),
        ],
        handler=mock_handler,
    )
    registry.register(tool)
    return registry


@pytest.fixture
def agent_loop(tool_registry):
    """Create an AgentLoop instance."""
    from src.intelligence.prompt_assembler import PromptAssembler
    from src.metacognition.tracer import ReasoningTracer

    assembler = PromptAssembler(max_context_tokens=2000)
    tracer = ReasoningTracer()

    with patch("src.intelligence.agent_loop.get_claude_client") as mock_get:
        mock_get.return_value = AsyncMock()
        loop = AgentLoop(
            tool_registry=tool_registry,
            assembler=assembler,
            tracer=tracer,
            cloud_model="test-model",
            max_iterations=3,
        )
    return loop


@pytest.fixture
def session():
    return SessionState(session_id="test-session", channel="cli", user_id="u1")


class TestAgentLoopBasic:
    """Test basic agent loop functionality."""

    @pytest.mark.asyncio
    async def test_simple_response_no_tools(self, agent_loop, session):
        """Test a simple LLM response without tool calls."""
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value=make_response("Hello! How can I help?"))
        agent_loop._client = mock_client

        result = await agent_loop.run(session=session, user_message="Hello", use_tools=False)

        assert isinstance(result, AgentResponse)
        assert "Hello" in result.content
        assert result.tokens_in == 100
        assert result.tokens_out == 50

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self, agent_loop, session):
        """LLM calls a tool, gets results, then responds."""
        call_count = 0

        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_tool_response("test_tool", '{"query":"test"}')
            else:
                return make_response("Here are the results!")

        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=mock_completion)
        agent_loop._client = mock_client

        result = await agent_loop.run(session=session, user_message="search for test")

        assert result.content == "Here are the results!"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_strips_thinking_tags(self, agent_loop, session):
        """Qwen3 thinking tags are stripped from response."""
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value=make_response("<think>thinking...</think>Final answer"))
        agent_loop._client = mock_client

        result = await agent_loop.run(session=session, user_message="test")

        assert result.content == "Final answer"
        assert "<think>" not in result.content


class TestAgentLoopResilience:
    """Test error handling and retry logic."""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback(self, agent_loop, session):
        """When LLM fails all retries, return fallback response."""
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=RuntimeError("API down"))
        agent_loop._client = mock_client

        result = await agent_loop.run(session=session, user_message="test")

        assert isinstance(result, AgentResponse)
        assert "Xin lỗi" in result.content

    @pytest.mark.asyncio
    async def test_auth_error_no_retry(self, agent_loop, session):
        """401/403 errors should not be retried."""
        call_count = 0

        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("401 Unauthorized")

        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=mock_completion)
        agent_loop._client = mock_client

        result = await agent_loop.run(session=session, user_message="test")

        assert call_count == 1  # No retry for auth errors

    @pytest.mark.asyncio
    async def test_truncated_response_detected(self, agent_loop, session):
        """finish_reason='length' should add truncation warning."""
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value=make_response(
            "Partial response...", finish_reason="length",
        ))
        agent_loop._client = mock_client

        result = await agent_loop.run(session=session, user_message="write essay")

        assert "Partial response..." in result.content
        assert "bị cắt" in result.content

    @pytest.mark.asyncio
    async def test_max_iterations_returns_summary(self, agent_loop, session):
        """When max iterations reached, return work summary."""
        agent_loop._max_iterations = 2

        async def mock_completion(**kwargs):
            return make_tool_response("test_tool", '{"query":"loop"}')

        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=mock_completion)
        agent_loop._client = mock_client

        result = await agent_loop.run(session=session, user_message="search forever")

        # Should have summary of tool calls made
        assert "test_tool" in result.content or "Xin lỗi" in result.content

    @pytest.mark.asyncio
    async def test_partial_work_preserved_on_failure(self, agent_loop, session):
        """If LLM fails after tool calls, show work done so far."""
        call_count = 0

        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: tool call succeeds
                return make_tool_response("test_tool", '{"query":"step1"}')
            else:
                # Second call: LLM fails
                raise RuntimeError("API down after tool call")

        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=mock_completion)
        agent_loop._client = mock_client

        result = await agent_loop.run(session=session, user_message="multi-step")

        # Should mention the tool call that was made
        assert "test_tool" in result.content


class TestSerializeAssistantMessage:
    """Test clean serialization of assistant messages."""

    def test_text_only_message(self):
        msg = MockMessage(content="Hello!", tool_calls=[])
        serialized = AgentLoop._serialize_assistant_message(msg)
        assert serialized == {"role": "assistant", "content": "Hello!"}
        assert "tool_calls" not in serialized

    def test_message_with_tool_calls(self):
        tc = MockToolCall(
            id="call_123",
            function=MockFunction(name="web_search", arguments='{"query":"test"}'),
        )
        msg = MockMessage(content="", tool_calls=[tc])
        serialized = AgentLoop._serialize_assistant_message(msg)

        assert serialized["role"] == "assistant"
        assert len(serialized["tool_calls"]) == 1
        assert serialized["tool_calls"][0]["id"] == "call_123"
        assert serialized["tool_calls"][0]["type"] == "function"
        assert serialized["tool_calls"][0]["function"]["name"] == "web_search"

    def test_none_content_becomes_empty_string(self):
        msg = MockMessage(content=None, tool_calls=[])
        serialized = AgentLoop._serialize_assistant_message(msg)
        assert serialized["content"] == ""

    def test_none_arguments_becomes_empty_json(self):
        tc = MockToolCall(
            id="call_1",
            function=MockFunction(name="test", arguments=None),
        )
        msg = MockMessage(content="", tool_calls=[tc])
        serialized = AgentLoop._serialize_assistant_message(msg)
        assert serialized["tool_calls"][0]["function"]["arguments"] == "{}"


class TestBuildFallbackResponse:
    """Test fallback response building when max iterations reached."""

    @pytest.fixture
    def loop(self, tool_registry):
        from src.intelligence.prompt_assembler import PromptAssembler
        from src.metacognition.tracer import ReasoningTracer

        with patch("src.intelligence.agent_loop.get_claude_client") as mock_get:
            mock_get.return_value = AsyncMock()
            return AgentLoop(
                tool_registry=tool_registry,
                assembler=PromptAssembler(max_context_tokens=2000),
                tracer=ReasoningTracer(),
            )

    def test_no_tool_calls(self, loop):
        result = loop._build_fallback_response([])
        assert "Xin lỗi" in result

    def test_all_successful(self, loop):
        calls = [
            {"tool": "web_search", "args": {"query": "test"}, "success": True, "time_ms": 500},
        ]
        result = loop._build_fallback_response(calls)
        assert "✅" in result
        assert "web_search" in result

    def test_mixed_results(self, loop):
        calls = [
            {"tool": "web_search", "args": {}, "success": True, "time_ms": 500},
            {"tool": "run_python", "args": {}, "success": False, "time_ms": 100},
        ]
        result = loop._build_fallback_response(calls)
        assert "✅" in result
        assert "❌" in result
        assert "1/2" in result  # failure count

    def test_all_failed(self, loop):
        calls = [
            {"tool": "t1", "args": {}, "success": False, "time_ms": 100},
            {"tool": "t2", "args": {}, "success": False, "time_ms": 200},
        ]
        result = loop._build_fallback_response(calls)
        assert "lỗi" in result
