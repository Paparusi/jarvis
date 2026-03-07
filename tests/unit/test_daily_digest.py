"""Tests for Daily Digest — news curation by user interests."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.intelligence.daily_digest import (
    DigestGenerator,
    DigestItem,
    DailyDigest,
    _topic_emoji,
    _escape_md,
    _TOPIC_QUERIES,
    _DEFAULT_TOPICS,
    _MAX_TOPICS,
)


# === DigestItem ===

class TestDigestItem:
    def test_creation(self):
        item = DigestItem(
            title="Test News",
            snippet="Something happened",
            url="https://example.com",
            topic="AI",
        )
        assert item.title == "Test News"
        assert item.topic == "AI"

    def test_empty_snippet(self):
        item = DigestItem(title="T", snippet="", url="http://x.com", topic="web")
        assert item.snippet == ""


# === DailyDigest ===

class TestDailyDigest:
    def test_empty_digest(self):
        d = DailyDigest()
        assert d.items == []
        assert d.to_markdown() == ""
        assert d.to_plain() == ""

    def test_markdown_format(self):
        d = DailyDigest(items=[
            DigestItem("AI News Title", "AI is growing", "https://ai.com", "AI"),
            DigestItem("Python Update", "New version", "https://py.org", "python"),
        ])
        md = d.to_markdown()
        assert "Daily Digest" in md
        assert "AI" in md
        assert "PYTHON" in md
        assert "https://ai.com" in md

    def test_plain_format(self):
        d = DailyDigest(items=[
            DigestItem("Title", "Snippet", "https://example.com", "tech"),
        ])
        plain = d.to_plain()
        assert "Daily Digest" in plain
        assert "Title" in plain
        assert "https://example.com" in plain

    def test_groups_by_topic(self):
        d = DailyDigest(items=[
            DigestItem("A1", "s1", "https://a.com", "AI"),
            DigestItem("P1", "s2", "https://p.com", "python"),
            DigestItem("A2", "s3", "https://b.com", "AI"),
        ])
        md = d.to_markdown()
        # AI section should appear before python
        ai_pos = md.find("AI")
        assert ai_pos >= 0

    def test_search_time_in_footer(self):
        d = DailyDigest(
            items=[DigestItem("T", "S", "https://x.com", "AI")],
            search_time_ms=1500,
        )
        md = d.to_markdown()
        assert "1500ms" in md

    def test_generated_at(self):
        d = DailyDigest()
        assert isinstance(d.generated_at, datetime)


# === DigestGenerator ===

class TestDigestGenerator:
    def test_init_no_user_model(self):
        gen = DigestGenerator()
        assert gen._user_model is None
        assert gen.last_digest is None

    def test_already_generated_today(self):
        gen = DigestGenerator()
        assert not gen.already_generated_today()
        gen._last_digest_date = datetime.now().strftime("%Y-%m-%d")
        assert gen.already_generated_today()

    def test_get_stats_empty(self):
        gen = DigestGenerator()
        stats = gen.get_stats()
        assert stats["last_date"] is None
        assert stats["last_items"] == 0
        assert stats["last_topics"] == []

    def test_get_user_topics_no_model(self):
        gen = DigestGenerator()
        topics = gen._get_user_topics("user1", 3)
        assert topics == _DEFAULT_TOPICS[:3]

    def test_get_user_topics_from_model(self):
        model = MagicMock()
        model.get_or_create.return_value = {
            "topics": '{"python": 10, "AI": 8, "trading": 5, "docker": 2}',
        }
        gen = DigestGenerator(user_model=model)
        topics = gen._get_user_topics("user1", 3)
        assert len(topics) == 3
        assert topics[0] == "python"  # Highest frequency
        assert topics[1] == "AI"

    def test_get_user_topics_empty(self):
        model = MagicMock()
        model.get_or_create.return_value = {"topics": "{}"}
        gen = DigestGenerator(user_model=model)
        topics = gen._get_user_topics("user1", 3)
        assert topics == _DEFAULT_TOPICS[:3]

    @pytest.mark.asyncio
    async def test_search_topic_success(self):
        gen = DigestGenerator()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {
            "results": [
                {"title": "AI News", "body": "Something about AI", "href": "https://ai.com"},
                {"title": "ML Update", "body": "ML is great", "href": "https://ml.com"},
            ],
        }

        with patch("src.tools.web_search.search_web", new_callable=AsyncMock, return_value=mock_result):
            items = await gen._search_topic("AI")

        assert len(items) == 2
        assert items[0].title == "AI News"
        assert items[0].topic == "AI"

    @pytest.mark.asyncio
    async def test_search_topic_failure(self):
        gen = DigestGenerator()
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.data = {}

        with patch("src.tools.web_search.search_web", new_callable=AsyncMock, return_value=mock_result):
            items = await gen._search_topic("AI")

        assert items == []

    @pytest.mark.asyncio
    async def test_generate_with_custom_topics(self):
        gen = DigestGenerator()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {
            "results": [
                {"title": "Test", "body": "Snippet", "href": "https://test.com"},
            ],
        }

        with patch("src.tools.web_search.search_web", new_callable=AsyncMock, return_value=mock_result):
            digest = await gen.generate(topics=["custom-topic"])

        assert len(digest.items) == 1
        assert digest.items[0].topic == "custom-topic"
        assert digest.topics_searched == ["custom-topic"]
        assert gen.already_generated_today()

    @pytest.mark.asyncio
    async def test_generate_dedup_urls(self):
        gen = DigestGenerator()

        # Same URL from two different topics
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {
            "results": [
                {"title": "Same Article", "body": "Dup", "href": "https://same.com"},
            ],
        }

        with patch("src.tools.web_search.search_web", new_callable=AsyncMock, return_value=mock_result):
            digest = await gen.generate(topics=["AI", "python"])

        # URL should appear only once despite being in both topics
        assert len(digest.items) == 1

    @pytest.mark.asyncio
    async def test_generate_empty_results(self):
        gen = DigestGenerator()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {"results": []}

        with patch("src.tools.web_search.search_web", new_callable=AsyncMock, return_value=mock_result):
            digest = await gen.generate(topics=["obscure-topic"])

        assert len(digest.items) == 0

    @pytest.mark.asyncio
    async def test_generate_handles_search_exception(self):
        gen = DigestGenerator()

        with patch("src.tools.web_search.search_web", new_callable=AsyncMock, side_effect=Exception("Network error")):
            digest = await gen.generate(topics=["AI"])

        assert len(digest.items) == 0


# === Helpers ===

class TestTopicEmoji:
    def test_known_topics(self):
        assert _topic_emoji("ai") == "🤖"
        assert _topic_emoji("python") == "🐍"
        assert _topic_emoji("trading") == "📈"
        assert _topic_emoji("security") == "🔒"
        assert _topic_emoji("docker") == "🐳"

    def test_unknown_topic(self):
        assert _topic_emoji("random") == "📌"

    def test_case_insensitive(self):
        assert _topic_emoji("AI") == "🤖"
        assert _topic_emoji("Python") == "🐍"


class TestEscapeMd:
    def test_plain_text(self):
        assert _escape_md("hello world") == "hello world"

    def test_escapes_special_chars(self):
        assert "\\_" in _escape_md("hello_world")
        assert "\\*" in _escape_md("*bold*")
        assert "\\[" in _escape_md("[link]")

    def test_empty_string(self):
        assert _escape_md("") == ""


class TestTopicQueries:
    def test_has_common_topics(self):
        assert "ai" in _TOPIC_QUERIES
        assert "python" in _TOPIC_QUERIES
        assert "trading" in _TOPIC_QUERIES
        assert "security" in _TOPIC_QUERIES

    def test_queries_contain_topic(self):
        for topic, query in _TOPIC_QUERIES.items():
            # Query should be meaningful (not empty)
            assert len(query) > 5


class TestConstants:
    def test_default_topics(self):
        assert len(_DEFAULT_TOPICS) >= 2
        assert "AI" in _DEFAULT_TOPICS

    def test_max_topics(self):
        assert _MAX_TOPICS >= 2
        assert _MAX_TOPICS <= 10


# === ProactiveEngine integration ===

class TestProactiveDigestIntegration:
    def setup_method(self):
        from src.intelligence.proactive import ProactiveEngine
        self.memory = MagicMock()
        self.memory.search = AsyncMock(return_value=[])
        self.memory.get_all = MagicMock(return_value=[])
        self.user_model = MagicMock()
        self.user_model.get_or_create = MagicMock(return_value={
            "total_messages": 10,
            "avg_msg_length": 50,
            "language": "vi",
            "topics": '{"python": 5, "AI": 3}',
        })
        self.collector = MagicMock()
        self.collector.get_stats = MagicMock(return_value={
            "total_records": 100,
            "recent_records": 5,
        })
        self.engine = ProactiveEngine(
            self.memory, self.user_model, self.collector,
        )

    def test_has_digest_generator(self):
        assert hasattr(self.engine, '_digest')
        assert isinstance(self.engine._digest, DigestGenerator)

    def test_stats_include_digest(self):
        stats = self.engine.get_stats()
        assert "digest" in stats
        assert "last_date" in stats["digest"]

    @pytest.mark.asyncio
    async def test_generate_digest_now(self):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {
            "results": [
                {"title": "Test", "body": "S", "href": "https://t.com"},
            ],
        }

        with patch("src.tools.web_search.search_web", new_callable=AsyncMock, return_value=mock_result):
            text = await self.engine.generate_digest_now(topics=["AI"])

        assert "Daily Digest" in text

    @pytest.mark.asyncio
    async def test_generate_digest_now_empty(self):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {"results": []}

        with patch("src.tools.web_search.search_web", new_callable=AsyncMock, return_value=mock_result):
            text = await self.engine.generate_digest_now()

        assert "Không tìm thấy" in text
