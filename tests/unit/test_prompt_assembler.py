"""Tests for Prompt Assembler — context construction."""

import pytest

from src.intelligence.prompt_assembler import PromptAssembler, estimate_tokens


class TestEstimateTokens:
    def test_short_text(self):
        tokens = estimate_tokens("Hello world")
        assert 2 <= tokens <= 5

    def test_empty_text(self):
        assert estimate_tokens("") == 0


class TestPromptAssembler:
    def setup_method(self):
        self.assembler = PromptAssembler(
            max_context_tokens=6000,
            skill_summary="## Skills:\n- code-assistant: Viết code",
        )

    @pytest.mark.asyncio
    async def test_basic_assembly(self):
        messages = await self.assembler.assemble(
            user_message="Xin chào",
            history=[],
        )
        assert len(messages) >= 2  # system + user
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Xin chào"

    @pytest.mark.asyncio
    async def test_system_contains_base_prompt(self):
        messages = await self.assembler.assemble("test", [])
        system = messages[0]["content"]
        assert "JARVIS" in system

    @pytest.mark.asyncio
    async def test_system_has_datetime(self):
        messages = await self.assembler.assemble("test", [])
        system = messages[0]["content"]
        assert "Thời gian hiện tại" in system

    @pytest.mark.asyncio
    async def test_system_has_personality(self):
        messages = await self.assembler.assemble("test", [])
        system = messages[0]["content"]
        # v2: personality comes from JARVIS.md (if exists) or fallback
        assert "JARVIS" in system
        assert "TOOL CALLING" in system

    @pytest.mark.asyncio
    async def test_system_contains_skill_summary(self):
        messages = await self.assembler.assemble("test", [])
        system = messages[0]["content"]
        assert "code-assistant" in system

    @pytest.mark.asyncio
    async def test_memory_context_injected(self):
        messages = await self.assembler.assemble(
            user_message="tôi thích gì?",
            history=[],
            memory_context="User thích Python và cà phê đen",
        )
        system = messages[0]["content"]
        assert "BỘ NHỚ" in system
        assert "Python" in system

    @pytest.mark.asyncio
    async def test_skill_context_injected(self):
        messages = await self.assembler.assemble(
            user_message="viết code",
            history=[],
            skill_context="## Code Assistant\nViết code sạch theo PEP8",
        )
        system = messages[0]["content"]
        assert "PEP8" in system

    @pytest.mark.asyncio
    async def test_history_included(self):
        history = [
            {"role": "user", "content": "Câu hỏi 1"},
            {"role": "assistant", "content": "Trả lời 1"},
        ]
        messages = await self.assembler.assemble("Câu hỏi 2", history)
        # system + 2 history + user
        assert len(messages) == 4
        assert messages[1]["content"] == "Câu hỏi 1"
        assert messages[2]["content"] == "Trả lời 1"

    @pytest.mark.asyncio
    async def test_history_trimmed_on_budget(self):
        # Create very long history
        history = [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": f"Message {i}: " + "x" * 500}
            for i in range(100)
        ]
        assembler = PromptAssembler(max_context_tokens=2000)
        messages = await assembler.assemble("new message", history)
        # Should have trimmed history to fit budget
        assert len(messages) < 100 + 2  # less than all history + system + user

    @pytest.mark.asyncio
    async def test_update_skill_summary(self):
        self.assembler.update_skill_summary("## New Skills:\n- web-research: Tìm kiếm")
        messages = await self.assembler.assemble("test", [])
        system = messages[0]["content"]
        assert "web-research" in system
