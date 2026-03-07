"""Tests for Event Bus."""

import pytest

from src.gateway.event_bus import Event, EventBus, EventType


class TestEventBus:
    def setup_method(self):
        self.bus = EventBus()
        self.received_events: list[Event] = []

    async def _handler(self, event: Event) -> None:
        self.received_events.append(event)

    async def _failing_handler(self, event: Event) -> None:
        raise ValueError("Handler failed!")

    @pytest.mark.asyncio
    async def test_publish_subscribe(self):
        self.bus.subscribe("test_event", self._handler)
        await self.bus.publish("test_event", {"key": "value"})
        assert len(self.received_events) == 1
        assert self.received_events[0].data["key"] == "value"

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        received2: list[Event] = []

        async def handler2(event: Event) -> None:
            received2.append(event)

        self.bus.subscribe("test_event", self._handler)
        self.bus.subscribe("test_event", handler2)
        await self.bus.publish("test_event")
        assert len(self.received_events) == 1
        assert len(received2) == 1

    @pytest.mark.asyncio
    async def test_no_crosstalk(self):
        self.bus.subscribe("event_a", self._handler)
        await self.bus.publish("event_b")
        assert len(self.received_events) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        self.bus.subscribe("test_event", self._handler)
        self.bus.unsubscribe("test_event", self._handler)
        await self.bus.publish("test_event")
        assert len(self.received_events) == 0

    @pytest.mark.asyncio
    async def test_handler_error_isolation(self):
        """One handler failing shouldn't affect others."""
        self.bus.subscribe("test_event", self._failing_handler)
        self.bus.subscribe("test_event", self._handler)
        await self.bus.publish("test_event")
        # _handler should still receive the event
        assert len(self.received_events) == 1

    @pytest.mark.asyncio
    async def test_event_history(self):
        await self.bus.publish("event_a", {"x": 1})
        await self.bus.publish("event_b", {"y": 2})
        await self.bus.publish("event_a", {"x": 3})

        history = self.bus.get_recent_events()
        assert len(history) == 3

        filtered = self.bus.get_recent_events("event_a")
        assert len(filtered) == 2

    @pytest.mark.asyncio
    async def test_event_source(self):
        self.bus.subscribe("test", self._handler)
        await self.bus.publish("test", source="memory")
        assert self.received_events[0].source == "memory"

    def test_stats(self):
        self.bus.subscribe("a", self._handler)
        self.bus.subscribe("b", self._handler)
        stats = self.bus.get_stats()
        assert stats["subscribers"]["a"] == 1
        assert stats["subscribers"]["b"] == 1

    def test_event_types_enum(self):
        assert EventType.MESSAGE_RECEIVED == "message_received"
        assert EventType.TOOL_CALLED == "tool_called"
        assert EventType.SKILL_MATCHED == "skill_matched"


class TestSummarizer:
    """Tests for ConversationSummarizer."""

    def setup_method(self):
        from src.memory.summarizer import ConversationSummarizer
        self.summarizer = ConversationSummarizer(
            keep_recent=5,
            trigger_threshold=10,
        )

    def test_no_summarization_needed(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
        assert not self.summarizer.needs_summarization(messages)

    def test_summarization_needed(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(15)]
        assert self.summarizer.needs_summarization(messages)

    @pytest.mark.asyncio
    async def test_summarize_short_returns_all(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
        summary, recent = await self.summarizer.summarize(messages)
        assert summary == ""
        assert recent == messages

    def test_format_conversation(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        text = self.summarizer._format_conversation(messages)
        assert "User: Hello" in text
        assert "JARVIS: Hi there!" in text

    def test_hash_messages(self):
        msgs1 = [{"role": "user", "content": "hello"}]
        msgs2 = [{"role": "user", "content": "hello"}]
        msgs3 = [{"role": "user", "content": "different"}]
        assert self.summarizer._hash_messages(msgs1) == self.summarizer._hash_messages(msgs2)
        assert self.summarizer._hash_messages(msgs1) != self.summarizer._hash_messages(msgs3)

    def test_build_summarized_history(self):
        recent = [
            {"role": "user", "content": "recent msg"},
        ]
        result = self.summarizer.build_summarized_history("Previous conversation summary", recent)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "summary" in result[0]["content"].lower()
        assert result[1]["content"] == "recent msg"

    def test_fallback_summary(self):
        long_text = "\n".join(f"Line {i}" for i in range(20))
        summary = self.summarizer._fallback_summary(long_text)
        assert "..." in summary
        assert "Line 0" in summary
