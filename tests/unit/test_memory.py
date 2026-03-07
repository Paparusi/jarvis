"""Tests for Memory Manager — intent detection, fact extraction."""

import pytest

from src.memory.manager import MemoryManager


class TestDetectRememberIntent:
    """Test Vietnamese + English memory intent detection patterns."""

    @pytest.fixture
    def mgr(self):
        return MemoryManager()

    @pytest.mark.asyncio
    async def test_explicit_remember_vi(self, mgr):
        await mgr._detect_remember_intent("nhớ giúp tôi thích cà phê đen")
        memories = mgr.semantic.get_all(limit=50)
        found = any("cà phê đen" in m["content"] for m in memories)
        assert found, f"Expected to find 'cà phê đen' in memories: {memories}"

    @pytest.mark.asyncio
    async def test_explicit_remember_en(self, mgr):
        await mgr._detect_remember_intent("remember that I like dark mode")
        memories = mgr.semantic.get_all(limit=50)
        found = any("dark mode" in m["content"] for m in memories)
        assert found, f"Expected to find 'dark mode' in memories: {memories}"

    @pytest.mark.asyncio
    async def test_naming_request(self, mgr):
        await mgr._detect_remember_intent("gọi tôi là Bi")
        memories = mgr.semantic.get_all(limit=50)
        found = any("Bi" in m["content"] for m in memories)
        assert found, f"Expected to find 'Bi' in memories: {memories}"

    @pytest.mark.asyncio
    async def test_identity_statement(self, mgr):
        await mgr._detect_remember_intent("tôi là chủ của Jarvis")
        memories = mgr.semantic.get_all(limit=50)
        found = any("chủ" in m["content"] for m in memories)
        assert found, f"Expected to find identity in memories: {memories}"

    @pytest.mark.asyncio
    async def test_no_false_positive_question(self, mgr):
        """'tôi là ai' should NOT be stored as a fact."""
        await mgr._detect_remember_intent("tôi là ai không?")
        memories = mgr.semantic.get_all(limit=50)
        # Should not store "ai" as identity
        bad = any("ai" == m["content"].strip() for m in memories)
        assert not bad, f"False positive: stored question as fact: {memories}"

    @pytest.mark.asyncio
    async def test_preference_detection(self, mgr):
        await mgr._detect_remember_intent("tôi thích Python hơn Java")
        memories = mgr.semantic.get_all(limit=200)
        found = any("Python" in m["content"] for m in memories)
        assert found, f"Expected to find preference in memories (checked {len(memories)})"

    @pytest.mark.asyncio
    async def test_directive_detection(self, mgr):
        await mgr._detect_remember_intent("từ nay trả lời bằng tiếng Anh")
        memories = mgr.semantic.get_all(limit=200)
        found = any("tiếng Anh" in m["content"] for m in memories)
        assert found, f"Expected to find directive in memories (checked {len(memories)})"

    @pytest.mark.asyncio
    async def test_no_match_normal_message(self, mgr):
        before_count = mgr.semantic.count()
        await mgr._detect_remember_intent("hôm nay trời đẹp quá")
        after_count = mgr.semantic.count()
        # Should not add any new memories from casual chat
        assert after_count == before_count, "Should not store casual chat as memory"
