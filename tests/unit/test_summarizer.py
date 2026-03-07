"""Tests for Conversation Summarizer — persistent summaries."""

import pytest

from src.memory.summarizer import ConversationSummarizer


class TestConversationSummarizer:
    @pytest.fixture
    def summarizer(self):
        return ConversationSummarizer(
            keep_recent=5,
            trigger_threshold=10,
        )

    def test_needs_summarization_short(self, summarizer):
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
        assert not summarizer.needs_summarization(msgs)

    def test_needs_summarization_long(self, summarizer):
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(15)]
        assert summarizer.needs_summarization(msgs)

    @pytest.mark.asyncio
    async def test_summarize_short_conversation(self, summarizer):
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
        summary, recent = await summarizer.summarize(msgs)
        assert summary == ""  # No summarization needed
        assert recent == msgs

    @pytest.mark.asyncio
    async def test_summarize_long_conversation(self, summarizer):
        """Should split into summary + recent messages."""
        msgs = []
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": f"Message number {i}"})

        summary, recent = await summarizer.summarize(msgs)
        # Should keep last 5 messages
        assert len(recent) == 5
        assert recent[0]["content"] == "Message number 10"
        # Summary should exist (even if fallback)
        assert len(summary) > 0

    def test_build_summarized_history(self, summarizer):
        summary = "User asked about Python. JARVIS helped with code."
        recent = [
            {"role": "user", "content": "thanks"},
            {"role": "assistant", "content": "you're welcome"},
        ]
        result = summarizer.build_summarized_history(summary, recent)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert "Tóm tắt" in result[0]["content"]
        assert result[1]["content"] == "thanks"

    def test_build_summarized_history_no_summary(self, summarizer):
        recent = [{"role": "user", "content": "hi"}]
        result = summarizer.build_summarized_history("", recent)
        assert len(result) == 1

    def test_format_conversation(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        text = ConversationSummarizer._format_conversation(msgs)
        assert "User: hello" in text
        assert "JARVIS: hi there" in text

    def test_format_conversation_truncates_long_messages(self):
        msgs = [{"role": "user", "content": "a" * 500}]
        text = ConversationSummarizer._format_conversation(msgs)
        assert "..." in text
        assert len(text) < 300

    def test_hash_messages(self):
        msgs1 = [{"role": "user", "content": "hello"}]
        msgs2 = [{"role": "user", "content": "hello"}]
        msgs3 = [{"role": "user", "content": "world"}]
        assert ConversationSummarizer._hash_messages(msgs1) == ConversationSummarizer._hash_messages(msgs2)
        assert ConversationSummarizer._hash_messages(msgs1) != ConversationSummarizer._hash_messages(msgs3)

    def test_fallback_summary(self, summarizer):
        text = "\n".join([f"Line {i}" for i in range(10)])
        summary = summarizer._fallback_summary(text)
        assert "Line 0" in summary
        assert "Line 9" in summary
        assert "..." in summary

    def test_get_session_summaries_empty(self, summarizer):
        summaries = summarizer.get_session_summaries("nonexistent_session")
        assert summaries == []

    @pytest.mark.asyncio
    async def test_persistent_summary(self, summarizer):
        """Summary should be saved to and loaded from DB."""
        msgs = []
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": f"Test persistent msg {i}"})

        session_key = "test:persistent_summary"

        # First summarization
        summary1, recent1 = await summarizer.summarize(msgs, session_key=session_key)
        assert len(summary1) > 0

        # Create new summarizer (simulating restart)
        new_summarizer = ConversationSummarizer(keep_recent=5, trigger_threshold=10)

        # Should load from DB (not re-generate)
        summary2, recent2 = await new_summarizer.summarize(msgs, session_key=session_key)
        assert summary2 == summary1

    @pytest.mark.asyncio
    async def test_session_summaries_persist(self, summarizer):
        """get_session_summaries should return persisted summaries."""
        msgs = []
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": f"Session summary test {i}"})

        session_key = "test:session_summaries"
        await summarizer.summarize(msgs, session_key=session_key)

        summaries = summarizer.get_session_summaries(session_key)
        assert len(summaries) >= 1
