"""Tests for ConversationTracker — intent & topic tracking."""

import pytest

from src.intelligence.intent_tracker import ConversationTracker


@pytest.fixture
def tracker():
    return ConversationTracker()


class TestIntentDetection:
    def test_detect_debugging_intent(self, tracker):
        tracker.update("s1", "lỗi gì đây, sao nó không chạy")
        state = tracker.get_state("s1")
        assert state["current_intent"] == "debugging"

    def test_detect_learning_intent(self, tracker):
        tracker.update("s1", "giải thích cho tao transformer là gì")
        state = tracker.get_state("s1")
        assert state["current_intent"] == "learning"

    def test_detect_building_intent(self, tracker):
        tracker.update("s1", "viết code tạo API endpoint mới")
        state = tracker.get_state("s1")
        assert state["current_intent"] == "building"

    def test_detect_researching_intent(self, tracker):
        tracker.update("s1", "so sánh React vs Vue nên dùng cái nào")
        state = tracker.get_state("s1")
        assert state["current_intent"] == "researching"

    def test_detect_chatting_intent(self, tracker):
        tracker.update("s1", "xin chào bạn")
        state = tracker.get_state("s1")
        # chatting doesn't overwrite current_intent (stays empty)
        assert state["current_intent"] == ""

    def test_unknown_defaults_chatting(self, tracker):
        tracker.update("s1", "hmm ok")
        state = tracker.get_state("s1")
        assert state["message_count"] == 1


class TestTopicDetection:
    def test_detect_python_topic(self, tracker):
        tracker.update("s1", "help me fix this python function")
        state = tracker.get_state("s1")
        assert state["current_topic"] == "python"

    def test_detect_ai_ml_topic(self, tracker):
        tracker.update("s1", "how to fine-tune a transformer model")
        state = tracker.get_state("s1")
        assert state["current_topic"] == "ai_ml"

    def test_detect_trading_topic(self, tracker):
        tracker.update("s1", "xauusd giá bao nhiêu hôm nay")
        state = tracker.get_state("s1")
        assert state["current_topic"] == "trading"


class TestConversationTracking:
    def test_message_count(self, tracker):
        tracker.update("s1", "hello")
        tracker.update("s1", "help me debug")
        tracker.update("s1", "what is this error")
        state = tracker.get_state("s1")
        assert state["message_count"] == 3

    def test_topic_transition(self, tracker):
        tracker.update("s1", "help me fix this python bug")
        tracker.update("s1", "now let me check docker deployment")
        state = tracker.get_state("s1")
        assert state["topic_transitions"] == 1

    def test_intent_transition(self, tracker):
        tracker.update("s1", "debug this python error")
        tracker.update("s1", "viết code function mới")
        state = tracker.get_state("s1")
        assert state["intent_transitions"] == 1

    def test_separate_sessions(self, tracker):
        tracker.update("s1", "debug python code")
        tracker.update("s2", "tìm kiếm thông tin trading")
        assert tracker.get_state("s1")["current_topic"] == "python"
        assert tracker.get_state("s2")["current_topic"] == "trading"

    def test_build_context_insufficient_messages(self, tracker):
        tracker.update("s1", "hello")
        assert tracker.build_context("s1") == ""

    def test_build_context_with_data(self, tracker):
        tracker.update("s1", "debug python error")
        tracker.update("s1", "what is this traceback")
        ctx = tracker.build_context("s1")
        assert "Ngữ cảnh hội thoại" in ctx
        assert "debug" in ctx.lower() or "sửa lỗi" in ctx.lower()

    def test_reset_session(self, tracker):
        tracker.update("s1", "debug python")
        tracker.reset("s1")
        assert tracker.get_state("s1") == {}

    def test_empty_session(self, tracker):
        assert tracker.get_state("nonexistent") == {}

    def test_history_bounded(self, tracker):
        for i in range(60):
            tracker.update("s1", f"debug error {i}")
        state = tracker.get_state("s1")
        assert state["message_count"] == 60
