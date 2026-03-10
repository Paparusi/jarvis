"""Tests for Multi-agent Swarm — decomposer, factory, coordinator, aggregator, message bus."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

from src.gateway.models import AgentResponse, SessionState
from src.swarm.decomposer import (
    TaskDecomposer, TaskPlan, Subtask, SubtaskType,
)
from src.swarm.factory import AgentFactory, SwarmAgent, _TYPE_PROMPTS
from src.swarm.coordinator import SwarmCoordinator, SwarmResult
from src.swarm.aggregator import ResultAggregator, AggregatedResult
from src.swarm.message_bus import SwarmMessageBus


# --- Fixtures ---

@pytest.fixture
def session():
    return SessionState(session_id="test-sess", channel="cli", user_id="u1")


@pytest.fixture
def tool_registry():
    from src.tools.base import ToolRegistry
    registry = ToolRegistry()
    registry._event_bus = MagicMock()
    registry._event_bus.publish = AsyncMock()
    return registry


@pytest.fixture
def decomposer():
    return TaskDecomposer(model="test-model", complexity_threshold=30)


@pytest.fixture
def factory(tool_registry):
    return AgentFactory(
        tool_registry=tool_registry,
        cloud_model="test-model",
    )


# === TaskDecomposer Tests ===

class TestDecomposerHeuristics:
    """Test should_decompose heuristic checks."""

    def test_simple_short_message_no_decompose(self, decomposer):
        assert decomposer.should_decompose("Hello world") is False

    def test_long_message_decompose(self, decomposer):
        text = " ".join(["word"] * 35)  # > 30 words
        assert decomposer.should_decompose(text) is True

    def test_multi_signal_va(self, decomposer):
        assert decomposer.should_decompose(
            "Tìm thông tin về Python và phân tích kết quả rồi viết báo cáo chi tiết"
        ) is True

    def test_multi_signal_and(self, decomposer):
        assert decomposer.should_decompose(
            "Search for the latest data about AI models and then write a detailed report"
        ) is True

    def test_multi_signal_numbered_list(self, decomposer):
        assert decomposer.should_decompose("Do:\n1. search\n2. analyze") is True

    def test_no_signal_short(self, decomposer):
        assert decomposer.should_decompose("What time is it?") is False


class TestDecomposerSimple:
    """Test decompose() for simple requests."""

    @pytest.mark.asyncio
    async def test_simple_request_single_subtask(self, decomposer):
        plan = await decomposer.decompose("Hello")
        assert len(plan.subtasks) == 1
        assert plan.subtasks[0].description == "Hello"
        assert plan.parallel_groups == [["t1"]]

    @pytest.mark.asyncio
    async def test_simple_preserves_original(self, decomposer):
        msg = "What's the weather?"
        plan = await decomposer.decompose(msg)
        assert plan.original_request == msg


class TestDecomposerLLM:
    """Test LLM-based decomposition."""

    @pytest.mark.asyncio
    async def test_llm_decompose_parses_json(self, decomposer):
        from src.intelligence.llm_models import LLMResponse, Choice, Message
        llm_response = LLMResponse(
            choices=[Choice(message=Message(content='''{
            "subtasks": [
                {"id": "t1", "description": "Research topic", "type": "research", "depends_on": [], "tools_hint": ["web_search"], "priority": 1},
                {"id": "t2", "description": "Write summary", "type": "generation", "depends_on": ["t1"], "priority": 2}
            ],
            "parallel_groups": [["t1"], ["t2"]],
            "requires_synthesis": false
        }'''))],
        )

        with patch("src.swarm.decomposer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(return_value=llm_response)
            mock_get_client.return_value = mock_client
            plan = await decomposer.decompose("Research and " * 20)  # Long enough

        assert len(plan.subtasks) == 2
        assert plan.subtasks[0].type == SubtaskType.RESEARCH
        assert plan.subtasks[1].depends_on == ["t1"]
        assert len(plan.parallel_groups) == 2

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back(self, decomposer):
        with patch("src.swarm.decomposer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(side_effect=RuntimeError("API down"))
            mock_get_client.return_value = mock_client
            plan = await decomposer.decompose("Do many things " * 20)

        # Fallback: single subtask
        assert len(plan.subtasks) == 1


# === AgentFactory Tests ===

class TestAgentFactory:

    def test_creates_agent(self, factory, session):
        subtask = Subtask(id="t1", description="Test task", type=SubtaskType.RESEARCH)
        agent = factory.create(subtask, session)

        assert isinstance(agent, SwarmAgent)
        assert agent.id.startswith("agent_")
        assert "research" in agent.id
        assert agent.subtask is subtask

    def test_increments_counter(self, factory, session):
        s1 = Subtask(id="t1", description="A", type=SubtaskType.GENERATION)
        s2 = Subtask(id="t2", description="B", type=SubtaskType.ANALYSIS)
        factory.create(s1, session)
        factory.create(s2, session)

        assert factory.get_stats()["agents_created"] == 2

    def test_all_types_have_prompts(self):
        for task_type in SubtaskType:
            assert task_type in _TYPE_PROMPTS


# === SwarmCoordinator Tests ===

class TestSwarmCoordinator:

    @pytest.mark.asyncio
    async def test_single_subtask_no_decomposition(self, tool_registry, session):
        decomposer = TaskDecomposer(complexity_threshold=999)
        factory = AgentFactory(tool_registry=tool_registry)
        coordinator = SwarmCoordinator(decomposer=decomposer, factory=factory)

        mock_response = AgentResponse(
            request_id="r1", session_id="s1", content="Direct answer",
            tokens_in=10, tokens_out=20, latency_ms=100,
        )

        with patch.object(SwarmAgent, "execute", new_callable=AsyncMock, return_value=mock_response):
            result = await coordinator.execute(session, "Hello")

        assert isinstance(result, SwarmResult)
        assert result.final_content == "Direct answer"
        assert result.was_decomposed is False
        assert result.agents_used == 1

    @pytest.mark.asyncio
    async def test_multi_subtask_execution(self, tool_registry, session):
        decomposer = MagicMock()
        decomposer.decompose = AsyncMock(return_value=TaskPlan(
            original_request="do two things",
            subtasks=[
                Subtask(id="t1", description="Task 1", type=SubtaskType.RESEARCH),
                Subtask(id="t2", description="Task 2", type=SubtaskType.GENERATION),
            ],
            parallel_groups=[["t1", "t2"]],
            requires_synthesis=False,
        ))

        factory = AgentFactory(tool_registry=tool_registry)
        coordinator = SwarmCoordinator(decomposer=decomposer, factory=factory)

        mock_response = AgentResponse(
            request_id="r1", session_id="s1", content="Result content here",
            tokens_in=10, tokens_out=20, latency_ms=100,
        )

        with patch.object(SwarmAgent, "execute", new_callable=AsyncMock, return_value=mock_response):
            result = await coordinator.execute(session, "do two things")

        assert result.was_decomposed is True
        assert result.agents_used == 2
        assert len(result.subtask_results) == 2

    def test_stats(self, tool_registry):
        decomposer = TaskDecomposer()
        factory = AgentFactory(tool_registry=tool_registry)
        coordinator = SwarmCoordinator(decomposer=decomposer, factory=factory)

        stats = coordinator.get_stats()
        assert stats["total_runs"] == 0
        assert stats["max_parallel"] == 3
        assert stats["agent_timeout"] == 60.0

    @pytest.mark.asyncio
    async def test_result_includes_conflict_info(self, tool_registry, session):
        """Verify SwarmResult includes conflict and dedup fields."""
        decomposer = TaskDecomposer(complexity_threshold=999)
        factory = AgentFactory(tool_registry=tool_registry)
        coordinator = SwarmCoordinator(decomposer=decomposer, factory=factory)

        mock_response = AgentResponse(
            request_id="r1", session_id="s1", content="Answer",
            tokens_in=10, tokens_out=20, latency_ms=100,
        )

        with patch.object(SwarmAgent, "execute", new_callable=AsyncMock, return_value=mock_response):
            result = await coordinator.execute(session, "Hello")

        assert hasattr(result, "conflicts")
        assert hasattr(result, "duplicates_removed")
        assert hasattr(result, "retries")


# === ResultAggregator Tests ===

class TestResultAggregator:

    def test_empty_results(self):
        agg = ResultAggregator()
        plan = TaskPlan(
            original_request="test",
            subtasks=[Subtask(id="t1", description="Task")],
            parallel_groups=[["t1"]],
        )
        result = agg.aggregate(plan, {})
        assert result.successful_subtasks == 0
        assert "Không có" in result.content

    def test_single_result(self):
        agg = ResultAggregator()
        plan = TaskPlan(
            original_request="test",
            subtasks=[Subtask(id="t1", description="Task")],
            parallel_groups=[["t1"]],
        )
        result = agg.aggregate(plan, {"t1": "Good answer here"})
        assert result.successful_subtasks == 1
        assert "Good answer here" in result.content

    def test_multi_result_merged(self):
        agg = ResultAggregator()
        plan = TaskPlan(
            original_request="test",
            subtasks=[
                Subtask(id="t1", description="First task"),
                Subtask(id="t2", description="Second task"),
            ],
            parallel_groups=[["t1", "t2"]],
        )
        result = agg.aggregate(plan, {
            "t1": "Result from first task with detailed analysis",
            "t2": "Result from second task with good content",
        })
        assert result.successful_subtasks == 2
        assert "First task" in result.content
        assert "Second task" in result.content

    def test_quality_scoring(self):
        agg = ResultAggregator()
        # Short content
        assert agg._score_quality("ok") < 0.5
        # Longer structured content
        assert agg._score_quality("## Heading\n- Point 1\n- Point 2\n" * 5) >= 0.6
        # Error content
        assert agg._score_quality("Error: failed to process the request") < 0.4
        # Empty
        assert agg._score_quality("") == 0.0

    def test_deduplication(self):
        agg = ResultAggregator(dedup_threshold=0.7)
        plan = TaskPlan(
            original_request="test",
            subtasks=[
                Subtask(id="t1", description="Research A"),
                Subtask(id="t2", description="Research B"),
            ],
            parallel_groups=[["t1", "t2"]],
        )
        result = agg.aggregate(plan, {
            "t1": "Python is a programming language used for web development and data science.",
            "t2": "Python is a programming language used for web development and data science.",
        })
        assert result.duplicates_removed >= 1

    def test_conflict_detection(self):
        agg = ResultAggregator()
        plan = TaskPlan(
            original_request="test",
            subtasks=[
                Subtask(id="t1", description="Check A"),
                Subtask(id="t2", description="Check B"),
            ],
            parallel_groups=[["t1", "t2"]],
        )
        result = agg.aggregate(plan, {
            "t1": "The system is safe and working correctly",
            "t2": "The system is unsafe and needs fixing",
        })
        assert len(result.conflicts) >= 1
        assert "safe" in result.conflicts[0]["signal"]

    def test_no_false_conflicts(self):
        agg = ResultAggregator()
        plan = TaskPlan(
            original_request="test",
            subtasks=[
                Subtask(id="t1", description="A"),
                Subtask(id="t2", description="B"),
            ],
            parallel_groups=[["t1", "t2"]],
        )
        result = agg.aggregate(plan, {
            "t1": "Apples are red fruits",
            "t2": "Oranges are orange fruits",
        })
        assert len(result.conflicts) == 0


# === SwarmMessageBus Tests ===

class TestSwarmMessageBus:

    def test_publish_and_get(self):
        bus = SwarmMessageBus()
        bus.publish("agent_1", "finding", "Found relevant data")
        bus.publish("agent_2", "finding", "Another finding")

        msgs = bus.get_messages(topic="finding")
        assert len(msgs) == 2
        assert msgs[0].agent_id == "agent_1"
        assert msgs[1].content == "Another finding"

    def test_shared_knowledge(self):
        bus = SwarmMessageBus()
        bus.share_knowledge("key1", "value1")
        assert bus.get_knowledge("key1") == "value1"
        assert bus.get_knowledge("nonexistent") is None

    def test_url_dedup(self):
        bus = SwarmMessageBus()
        assert bus.is_url_fetched("https://example.com") is False
        bus.mark_url_fetched("https://example.com", "page content")
        assert bus.is_url_fetched("https://example.com") is True
        assert bus.get_knowledge("url:https://example.com") == "page content"

    def test_get_findings_excludes_self(self):
        bus = SwarmMessageBus()
        bus.publish("agent_1", "finding", "My finding")
        bus.publish("agent_2", "finding", "Other finding")

        findings = bus.get_findings(exclude_agent="agent_1")
        assert len(findings) == 1
        assert findings[0] == "Other finding"

    def test_get_findings_all(self):
        bus = SwarmMessageBus()
        bus.publish("agent_1", "finding", "F1")
        bus.publish("agent_2", "finding", "F2")

        findings = bus.get_findings()
        assert len(findings) == 2

    def test_filter_by_agent(self):
        bus = SwarmMessageBus()
        bus.publish("agent_1", "finding", "F1")
        bus.publish("agent_2", "error", "E1")
        bus.publish("agent_1", "error", "E2")

        msgs = bus.get_messages(agent_id="agent_1")
        assert len(msgs) == 2

    def test_stats(self):
        bus = SwarmMessageBus()
        bus.publish("a1", "finding", "F1")
        bus.publish("a1", "error", "E1")
        bus.share_knowledge("k1", "v1")
        bus.mark_url_fetched("https://test.com")

        stats = bus.get_stats()
        assert stats["total_messages"] == 2
        assert stats["topics"]["finding"] == 1
        assert stats["shared_knowledge_entries"] == 1  # k1 only (url without content not stored)
        assert stats["urls_fetched"] == 1


# === Text Similarity Tests ===

class TestTextSimilarity:

    def test_identical_texts(self):
        assert ResultAggregator._text_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        assert ResultAggregator._text_similarity("abc", "xyz") < 0.5

    def test_similar_texts(self):
        sim = ResultAggregator._text_similarity(
            "Python is great for data science",
            "Python is great for data analysis",
        )
        assert sim > 0.7
