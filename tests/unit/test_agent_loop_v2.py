"""Tests for Agent Loop v2 — planning hints + quality gate."""
from __future__ import annotations

import pytest

from src.intelligence.agent_loop import AgentLoop


class TestNeedsPlanning:
    """Test the planning detection heuristic."""

    def setup_method(self):
        # Use a minimal AgentLoop instance for testing static-like methods
        from unittest.mock import MagicMock
        self.loop = AgentLoop(
            tool_registry=MagicMock(),
            assembler=MagicMock(),
            tracer=MagicMock(),
        )

    def test_simple_greeting_no_planning(self):
        assert not self.loop._needs_planning("Xin chào")

    def test_simple_question_no_planning(self):
        assert not self.loop._needs_planning("Mấy giờ rồi?")

    def test_analyze_needs_planning(self):
        assert self.loop._needs_planning("Phân tích code này giúp tôi")

    def test_research_needs_planning(self):
        assert self.loop._needs_planning("Nghiên cứu về Docker security best practices")

    def test_compare_needs_planning(self):
        assert self.loop._needs_planning("So sánh React và Vue cho dự án mới")

    def test_plan_needs_planning(self):
        assert self.loop._needs_planning("Lập kế hoạch deploy ứng dụng lên production")

    def test_long_message_needs_planning(self):
        long_msg = " ".join(["word"] * 35)
        assert self.loop._needs_planning(long_msg)

    def test_english_signals_detected(self):
        assert self.loop._needs_planning("Analyze and compare the performance metrics")


class TestInjectPlanningHint:
    def setup_method(self):
        from unittest.mock import MagicMock
        self.loop = AgentLoop(
            tool_registry=MagicMock(),
            assembler=MagicMock(),
            tracer=MagicMock(),
        )

    def test_injects_for_complex_query(self):
        messages = [
            {"role": "system", "content": "System prompt here"},
            {"role": "user", "content": "Phân tích code"},
        ]
        result = self.loop._inject_planning_hint(messages, "Phân tích code")
        assert "[PLANNING]" in result[0]["content"]

    def test_skips_for_simple_query(self):
        messages = [
            {"role": "system", "content": "System prompt here"},
            {"role": "user", "content": "Hi"},
        ]
        result = self.loop._inject_planning_hint(messages, "Hi")
        assert "[PLANNING]" not in result[0]["content"]

    def test_does_not_modify_user_message(self):
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Phân tích dữ liệu"},
        ]
        result = self.loop._inject_planning_hint(messages, "Phân tích dữ liệu")
        assert result[1]["content"] == "Phân tích dữ liệu"

    def test_handles_empty_messages(self):
        result = self.loop._inject_planning_hint([], "test")
        assert result == []


class TestQualityCheck:
    def test_normal_content_unchanged(self):
        result = AgentLoop._quality_check("Hello, this is a normal response.", [])
        assert result == "Hello, this is a normal response."

    def test_empty_content_unchanged(self):
        result = AgentLoop._quality_check("", [])
        assert result == ""

    def test_web_search_without_citation_adds_note(self):
        tool_calls = [{"tool": "web_search", "success": True}]
        result = AgentLoop._quality_check("Kết quả là XYZ.", tool_calls)
        assert "tìm kiếm web" in result

    def test_web_search_with_citation_no_note(self):
        tool_calls = [{"tool": "web_search", "success": True}]
        result = AgentLoop._quality_check("Theo https://example.com, kết quả là XYZ.", tool_calls)
        assert "tìm kiếm web" not in result

    def test_web_search_with_source_keyword_no_note(self):
        tool_calls = [{"tool": "web_search", "success": True}]
        result = AgentLoop._quality_check("Theo nguồn tin, kết quả là XYZ.", tool_calls)
        assert "tìm kiếm web" not in result

    def test_no_web_search_no_note(self):
        tool_calls = [{"tool": "run_python", "success": True}]
        result = AgentLoop._quality_check("Result: 42", tool_calls)
        assert "tìm kiếm web" not in result

    def test_failed_tool_ignored(self):
        tool_calls = [{"tool": "web_search", "success": False}]
        result = AgentLoop._quality_check("Some text.", tool_calls)
        assert "tìm kiếm web" not in result


class TestPlanningSignals:
    def test_planning_signals_list_exists(self):
        assert len(AgentLoop._PLANNING_SIGNALS) > 0

    def test_contains_vietnamese_signals(self):
        vn_signals = [s for s in AgentLoop._PLANNING_SIGNALS if any(
            c in s for c in "áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệ"
        )]
        assert len(vn_signals) > 0, "Should have Vietnamese planning signals"

    def test_contains_english_signals(self):
        en_signals = [s for s in AgentLoop._PLANNING_SIGNALS if s.isascii()]
        assert len(en_signals) > 0, "Should have English planning signals"
