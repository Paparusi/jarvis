"""Tests for Tool System — schemas, defaults, validation, async web search."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.tools.base import ToolDefinition, ToolParameter, ToolResult, ToolRegistry


class TestToolSchema:
    """Test OpenAI-compatible schema generation."""

    def test_schema_includes_defaults(self):
        tool = ToolDefinition(
            name="test",
            description="A test tool",
            parameters=[
                ToolParameter(name="query", type="string", description="Search query"),
                ToolParameter(
                    name="limit", type="integer", description="Max results",
                    required=False, default=5,
                ),
            ],
        )
        schema = tool.to_openai_schema()
        params = schema["function"]["parameters"]
        assert params["properties"]["limit"]["default"] == 5
        assert "default" not in params["properties"]["query"]

    def test_schema_has_additional_properties_false(self):
        tool = ToolDefinition(
            name="test",
            description="A test tool",
            parameters=[
                ToolParameter(name="q", type="string", description="Query"),
            ],
        )
        schema = tool.to_openai_schema()
        params = schema["function"]["parameters"]
        assert params["additionalProperties"] is False

    def test_schema_required_list(self):
        tool = ToolDefinition(
            name="test",
            description="A test tool",
            parameters=[
                ToolParameter(name="a", type="string", description="Required param"),
                ToolParameter(name="b", type="integer", description="Optional", required=False),
            ],
        )
        schema = tool.to_openai_schema()
        assert schema["function"]["parameters"]["required"] == ["a"]

    def test_schema_enum_values(self):
        tool = ToolDefinition(
            name="test",
            description="A test tool",
            parameters=[
                ToolParameter(
                    name="mode", type="string", description="Mode",
                    enum=["fast", "slow"],
                ),
            ],
        )
        schema = tool.to_openai_schema()
        assert schema["function"]["parameters"]["properties"]["mode"]["enum"] == ["fast", "slow"]


class TestToolRegistryDefaults:
    """Test argument defaulting and type coercion in ToolRegistry."""

    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        # Suppress event bus errors in tests
        reg._event_bus = MagicMock()
        reg._event_bus.publish = AsyncMock()
        return reg

    def _make_tool(self, handler):
        return ToolDefinition(
            name="test_tool",
            description="Test",
            parameters=[
                ToolParameter(name="query", type="string", description="Query"),
                ToolParameter(
                    name="max_results", type="integer", description="Limit",
                    required=False, default=5,
                ),
                ToolParameter(
                    name="verbose", type="boolean", description="Verbose",
                    required=False, default=False,
                ),
            ],
            handler=handler,
        )

    @pytest.mark.asyncio
    async def test_applies_defaults_for_missing_params(self, registry):
        """Missing optional params get default values."""
        received_args = {}

        async def handler(**kwargs):
            received_args.update(kwargs)
            return ToolResult(success=True, output="ok")

        tool = self._make_tool(handler)
        registry.register(tool)
        await registry.execute("test_tool", query="hello")

        assert received_args["query"] == "hello"
        assert received_args["max_results"] == 5
        assert received_args["verbose"] is False

    @pytest.mark.asyncio
    async def test_coerces_string_to_integer(self, registry):
        """LLMs sometimes send '5' instead of 5."""
        received_args = {}

        async def handler(**kwargs):
            received_args.update(kwargs)
            return ToolResult(success=True, output="ok")

        tool = self._make_tool(handler)
        registry.register(tool)
        await registry.execute("test_tool", query="hello", max_results="3")

        assert received_args["max_results"] == 3
        assert isinstance(received_args["max_results"], int)

    @pytest.mark.asyncio
    async def test_coerces_string_to_boolean(self, registry):
        received_args = {}

        async def handler(**kwargs):
            received_args.update(kwargs)
            return ToolResult(success=True, output="ok")

        tool = self._make_tool(handler)
        registry.register(tool)
        await registry.execute("test_tool", query="hello", verbose="true")

        assert received_args["verbose"] is True

    @pytest.mark.asyncio
    async def test_tool_not_found(self, registry):
        result = await registry.execute("nonexistent")
        assert not result.success
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_tool_no_handler(self, registry):
        tool = ToolDefinition(name="no_handler", description="Test")
        registry.register(tool)
        result = await registry.execute("no_handler")
        assert not result.success
        assert "no handler" in result.error

    @pytest.mark.asyncio
    async def test_event_bus_failure_doesnt_block(self, registry):
        """Event bus errors should not prevent tool execution."""
        registry._event_bus.publish = AsyncMock(side_effect=RuntimeError("bus down"))

        async def handler(**kwargs):
            return ToolResult(success=True, output="still works")

        tool = ToolDefinition(
            name="resilient",
            description="Test",
            parameters=[],
            handler=handler,
        )
        registry.register(tool)
        result = await registry.execute("resilient")
        assert result.success
        assert result.output == "still works"

    @pytest.mark.asyncio
    async def test_tool_timeout(self, registry):
        async def slow_handler(**kwargs):
            await asyncio.sleep(10)
            return ToolResult(success=True, output="done")

        tool = ToolDefinition(
            name="slow",
            description="Test",
            parameters=[],
            handler=slow_handler,
            timeout_seconds=1,
        )
        registry.register(tool)
        result = await registry.execute("slow")
        assert not result.success
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_tool_exception(self, registry):
        async def bad_handler(**kwargs):
            raise ValueError("something broke")

        tool = ToolDefinition(
            name="bad",
            description="Test",
            parameters=[],
            handler=bad_handler,
        )
        registry.register(tool)
        result = await registry.execute("bad")
        assert not result.success
        assert "something broke" in result.error

    def test_get_schemas(self, registry):
        tool = ToolDefinition(
            name="t1",
            description="Test 1",
            parameters=[ToolParameter(name="q", type="string", description="Q")],
        )
        registry.register(tool)
        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "t1"

    def test_get_stats(self, registry):
        tool = ToolDefinition(name="t1", description="Test")
        registry.register(tool)
        stats = registry.get_stats()
        assert stats["registered_tools"] == 1
        assert "t1" in stats["tool_names"]


class TestWebSearch:
    """Test web search tool with async DuckDuckGo."""

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        from src.tools.web_search import search_web
        result = await search_web(query="")
        assert not result.success
        assert "trống" in result.error

    @pytest.mark.asyncio
    async def test_search_clamps_max_results(self):
        from src.tools.web_search import search_web

        with patch("src.tools.web_search._sync_ddg_search", return_value=[]) as mock:
            await search_web(query="test", max_results=100)
            # Should clamp to 10
            mock.assert_called_once_with("test", 10)

    @pytest.mark.asyncio
    async def test_search_runs_in_thread(self):
        """DuckDuckGo should run in a thread to not block event loop."""
        from src.tools.web_search import search_web

        mock_results = [
            {"title": "Test", "href": "https://example.com", "body": "Test body"}
        ]

        with patch("src.tools.web_search._sync_ddg_search", return_value=mock_results):
            result = await search_web(query="test query")

        assert result.success
        assert "Test" in result.output
        assert result.data["count"] == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        from src.tools.web_search import search_web

        with patch("src.tools.web_search._sync_ddg_search", return_value=[]):
            result = await search_web(query="xyznonexistent123")

        assert result.success
        assert "Không tìm thấy" in result.output

    @pytest.mark.asyncio
    async def test_search_handles_exception(self):
        from src.tools.web_search import search_web

        with patch(
            "src.tools.web_search._sync_ddg_search",
            side_effect=RuntimeError("network error"),
        ):
            result = await search_web(query="test")

        assert not result.success
        assert "network error" in result.error


class TestFetchUrl:
    """Test URL fetching with validation."""

    @pytest.mark.asyncio
    async def test_rejects_empty_url(self):
        from src.tools.web_search import fetch_url
        result = await fetch_url(url="")
        assert not result.success
        assert "trống" in result.error

    @pytest.mark.asyncio
    async def test_rejects_non_http_url(self):
        from src.tools.web_search import fetch_url
        result = await fetch_url(url="ftp://example.com/file")
        assert not result.success
        assert "http" in result.error.lower()

    @pytest.mark.asyncio
    async def test_rejects_no_hostname(self):
        from src.tools.web_search import fetch_url
        result = await fetch_url(url="http://")
        assert not result.success
        assert "hostname" in result.error.lower()

    def test_validate_url_valid(self):
        from src.tools.web_search import _validate_url
        assert _validate_url("https://example.com") is None
        assert _validate_url("http://example.com/path?q=1") is None

    def test_validate_url_invalid(self):
        from src.tools.web_search import _validate_url
        assert _validate_url("") is not None
        assert _validate_url("not-a-url") is not None
        assert _validate_url("ftp://files.example.com") is not None
