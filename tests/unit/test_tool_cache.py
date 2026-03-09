"""Tests for agent loop tool result caching."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.base import ToolResult


class TestToolResultCache:
    @pytest.fixture
    def agent_loop(self):
        """Create a minimal AgentLoop with just the cache-relevant attributes."""
        from src.intelligence.agent_loop import AgentLoop

        loop = AgentLoop.__new__(AgentLoop)
        loop._tool_result_cache = {}
        loop._TOOL_CACHE_TTL = 30
        loop._tools = MagicMock()
        loop._tools.execute = AsyncMock(
            return_value=ToolResult(output="fresh", success=True)
        )
        return loop

    @pytest.mark.asyncio
    async def test_first_call_executes_tool(self, agent_loop):
        result = await agent_loop._execute_tool_cached("code_search", {"query": "test"})
        assert result.output == "fresh"
        agent_loop._tools.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self, agent_loop):
        await agent_loop._execute_tool_cached("code_search", {"query": "test"})
        await agent_loop._execute_tool_cached("code_search", {"query": "test"})
        # Should only call execute once (second is cached)
        assert agent_loop._tools.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_different_args_not_cached(self, agent_loop):
        await agent_loop._execute_tool_cached("code_search", {"query": "test1"})
        await agent_loop._execute_tool_cached("code_search", {"query": "test2"})
        assert agent_loop._tools.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_different_tools_not_cached(self, agent_loop):
        await agent_loop._execute_tool_cached("code_search", {"query": "test"})
        await agent_loop._execute_tool_cached("csv_analyze", {"query": "test"})
        assert agent_loop._tools.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_expired_cache_re_executes(self, agent_loop):
        await agent_loop._execute_tool_cached("code_search", {"query": "test"})
        # Manually expire the cache entry
        for key in agent_loop._tool_result_cache:
            old_time = time.time() - 60  # 60s ago
            agent_loop._tool_result_cache[key] = (
                old_time,
                agent_loop._tool_result_cache[key][1],
            )
        await agent_loop._execute_tool_cached("code_search", {"query": "test"})
        assert agent_loop._tools.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_returns_same_result(self, agent_loop):
        result1 = await agent_loop._execute_tool_cached("code_search", {"query": "test"})
        # Change what execute would return (but it shouldn't be called again)
        agent_loop._tools.execute.return_value = ToolResult(
            output="different", success=True
        )
        result2 = await agent_loop._execute_tool_cached("code_search", {"query": "test"})
        assert result1.output == result2.output == "fresh"

    @pytest.mark.asyncio
    async def test_cache_max_size(self, agent_loop):
        # Fill cache with 55 entries
        for i in range(55):
            agent_loop._tools.execute.return_value = ToolResult(
                output=f"result_{i}", success=True
            )
            await agent_loop._execute_tool_cached("tool", {"i": i})
        assert len(agent_loop._tool_result_cache) <= 50

    @pytest.mark.asyncio
    async def test_cache_key_arg_order_independent(self, agent_loop):
        """Args with same keys but different insertion order should hit cache."""
        await agent_loop._execute_tool_cached("tool", {"a": 1, "b": 2})
        await agent_loop._execute_tool_cached("tool", {"b": 2, "a": 1})
        # sort_keys=True in json.dumps ensures same hash
        assert agent_loop._tools.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_empty_args(self, agent_loop):
        await agent_loop._execute_tool_cached("tool", {})
        await agent_loop._execute_tool_cached("tool", {})
        assert agent_loop._tools.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_nocache_tools_always_execute(self, agent_loop):
        """MT5/trading tools should NEVER be cached — always fresh data."""
        nocache_tools = ["mt5_get_price", "mt5_get_positions", "trade_status", "web_search"]
        for tool_name in nocache_tools:
            agent_loop._tools.execute.reset_mock()
            await agent_loop._execute_tool_cached(tool_name, {"q": "test"})
            await agent_loop._execute_tool_cached(tool_name, {"q": "test"})
            # Both calls should execute (no caching)
            assert agent_loop._tools.execute.call_count == 2, f"{tool_name} was incorrectly cached"
